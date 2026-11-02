# Test-only TLS certificate

`test_cert.pem` and `test_key.pem` are a self-signed certificate and its private key, used only by the
offline suite's HTTPS cases (AgentWiz 0.1.2 specification, Section 15.2). The key is a public test
fixture: nothing trusts it except the tests' own SSL context (`test_tls` in `tests/conftest.py`). A
repository secret scanner may flag it; it is a known test fixture. Neither file ships in the wheel.

Profile: RSA 2048, subject `CN=AgentWiz test only`, `subjectAltName` `IP:127.0.0.1, DNS:localhost`,
`basicConstraints` critical `CA:FALSE`, `keyUsage` critical `digitalSignature, keyEncipherment`,
`extendedKeyUsage` `serverAuth`, valid for 100 years from creation.

To regenerate, run in this folder:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 36500 -subj "/CN=AgentWiz test only" \
  -addext "subjectAltName=IP:127.0.0.1,DNS:localhost" -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment" -addext "extendedKeyUsage=serverAuth" \
  -keyout test_key.pem -out test_cert.pem
```
