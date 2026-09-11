import argparse
import datetime
import ipaddress
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

def _san(value):
    try:
        return x509.IPAddress(ipaddress.ip_address(value))
    except ValueError:
        return x509.DNSName(value)


def generate_self_signed_cert(cert_path="cert.pem", key_path="key.pem", hosts=None):
    hosts = list(dict.fromkeys(hosts or ["localhost", "127.0.0.1"]))
    # Generate private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    # Generate public key
    public_key = private_key.public_key()

    # Subject and Issuer are the same for self-signed
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, u"localhost"),
    ])

    now = datetime.datetime.now(datetime.timezone.utc)
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        public_key
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        now - datetime.timedelta(minutes=1)
    ).not_valid_after(
        # Valid for 1 year
        now + datetime.timedelta(days=365)
    ).add_extension(
        x509.SubjectAlternativeName([_san(host) for host in hosts]),
        critical=False,
    ).sign(private_key, hashes.SHA256())

    # Write private key
    with open(key_path, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    # Write certificate
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    print(f"Generated {cert_path} and {key_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a self-signed lab TLS certificate.")
    parser.add_argument("--cert", default="cert.pem", help="certificate output path")
    parser.add_argument("--key", default="key.pem", help="private-key output path")
    parser.add_argument(
        "--host",
        action="append",
        dest="hosts",
        help="DNS name or IP SAN (repeatable; defaults to localhost and 127.0.0.1)",
    )
    args = parser.parse_args()
    generate_self_signed_cert(args.cert, args.key, args.hosts)
