import sys
sys.path.extend(['/Users/ferezgaetan/PycharmProjects/manpki/lib/ManPKI'])

__author__ = 'ferezgaetan'

from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from SocketServer import ThreadingMixIn
import threading
import sys
import daemonocle
import urlparse
import OpenSSL
import hashlib
import logging
from Crypto.PublicKey import RSA
from OpenSSL._util import lib as cryptolib
from pyasn1.codec.der import encoder, decoder
from pyasn1.type import useful
from asn1 import pem
from asn1 import rfc2560
from asn1 import rfc2459

def BytesToBin(bytes):
    """Convert byte string to bit string."""
    return "".join([_PadByte(IntToBin(ord(byte))) for byte in bytes])

def _PadByte(bits):
    """Pad a string of bits with zeros to make its length a multiple of 8."""
    r = len(bits) % 8
    return ((8-r) % 8)*'0' + bits

def IntToBin(n):
  if n == 0 or n == 1:
    return str(n)
  elif n % 2 == 0:
    return IntToBin(n/2) + "0"
  else:
    return IntToBin(n/2) + "1"

def pem_publickey(pkey):
    """ Format a public key as a PEM """
    bio = OpenSSL.crypto._new_mem_buf()
    cryptolib.PEM_write_bio_PUBKEY(bio, pkey._pkey)
    return OpenSSL.crypto._bio_to_string(bio)


class HTTPRequestHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        parsed_path = urlparse.urlparse(self.path)
        if self.headers['content-type'] in "application/ocsp-request":
            content = self.rfile.read(int(self.headers['content-length']))
            request, rest = decoder.decode(content, asn1Spec=rfc2560.OCSPRequest())

            tbsRequest = request.getComponentByName('tbsRequest')
            reqExt = tbsRequest.getComponentByName('requestExtensions')
            #TODO manage multiple request cert
            certRequest = tbsRequest.getComponentByName('requestList').getComponentByPosition(0)
            reqCert = certRequest.getComponentByName('reqCert')

            #TODO get ocsp server certificate from ManPKI cert store
            ocsp_cert, rt = decoder.decode(
                pem.readPemBlocksFromFile(
                    open('ocsp_srv.crt', 'r'), ('-----BEGIN CERTIFICATE-----', '-----END CERTIFICATE-----')
                )[1],
                asn1Spec=rfc2459.Certificate()
            )
            ocsp_key = OpenSSL.crypto.load_privatekey(OpenSSL.crypto.FILETYPE_PEM, open('ocsp_srv.key', "rt").read())
            str_pub_key = pem_publickey(ocsp_key)
            rsa_key = RSA.importKey(str_pub_key).exportKey(format='DER')
            hexrsa = hashlib.sha1(rsa_key).hexdigest()

            basic_ocsp_response = rfc2560.BasicOCSPResponse()
            tbs_response_data = basic_ocsp_response.setComponentByName('tbsResponseData').getComponentByName('tbsResponseData')

            responderid = tbs_response_data.setComponentByName('responderID').getComponentByName('responderID')
            responderid.setComponentByName('byKey', hexrsa.decode('hex'))

            tbs_response_data.setComponentByName('producedAt', useful.GeneralizedTime("20150728210000Z"))
            response_list = tbs_response_data.setComponentByName('responses').getComponentByName('responses')

            response = response_list.setComponentByPosition(0).getComponentByPosition(0)
            response.setComponentByName('certID', reqCert)

            certStatus = response.setComponentByName('certStatus').getComponentByName('certStatus')
            certStatus.setComponentByName('good')

            response.setComponentByName('thisUpdate', useful.GeneralizedTime("20150728211000Z"))

            signalgorithm = basic_ocsp_response.setComponentByName('signatureAlgorithm').getComponentByName('signatureAlgorithm')
            signalgorithm.setComponentByName('algorithm', rfc2459.sha1WithRSAEncryption)
            signalgorithm.setComponentByName('parameters', b'\x05\x00')

            hashsha1 = hashlib.sha1(encoder.encode(tbs_response_data))

            sign = OpenSSL.crypto.sign(ocsp_key, hashsha1.digest(), 'sha1')

            basic_ocsp_response.setComponentByName('signature', ("'%s'B" % BytesToBin(sign)))
            #certs = basic_ocsp_response.setComponentByName('certs').getComponentByName('certs')
            #certs.setComponentByPosition(0, ocsp_cert)

            ocsp_response = rfc2560.OCSPResponse()
            ocsp_response.setComponentByName('responseStatus', rfc2560.OCSPResponseStatus('successful'))

            response_bytes = ocsp_response.setComponentByName('responseBytes').getComponentByName('responseBytes')
            response_bytes.setComponentByName('responseType', rfc2560.id_pkix_ocsp_basic)
            response_bytes.setComponentByName('response', encoder.encode(basic_ocsp_response))

            status_code = 200
        else:
            ocsp_response = rfc2560.OCSPResponse()
            ocsp_response.setComponentByName('responseStatus', rfc2560.OCSPResponseStatus('malformedRequest'))
            status_code = 400

        self.send_response(status_code)
        self.send_header('Content-type', 'application/ocsp-response')
        self.end_headers()
        self.wfile.write(encoder.encode(ocsp_response))

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True

    def shutdown(self):
        self.socket.close()
        HTTPServer.shutdown(self)


class SimpleHttpServer():
    def __init__(self, ip, port):

        self.server = ThreadedHTTPServer((ip, port), HTTPRequestHandler)

    def start(self):

        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.daemon = True
        self.server_thread.start()

    def waitForThread(self):

        self.server_thread.join()

    def stop(self):

        self.server.shutdown()
        self.waitForThread()


def cb_shutdown(message, code):
    logging.info('Daemon is stopping')
    logging.debug(message)

def main():
    """This is my awesome daemon. It pretends to do work in the background."""
    logging.basicConfig(
        filename='/tmp/ocspd.log',
        level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(message)s',
    )
    logging.info('Daemon is starting')
    server = SimpleHttpServer("localhost", 2573)
    print 'OCSP Responder Running...........'
    server.start()
    server.waitForThread()

if __name__ == '__main__':
    daemon = daemonocle.Daemon(
        worker=main,
        detach=True,
        shutdown_callback=cb_shutdown,
        pidfile='/tmp/ocsp_daemon.pid',
    )
    daemon.do_action(sys.argv[1])

