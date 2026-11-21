#!/bin/sh
set -eu
umask 022
cd /inputs
sha256sum --strict --check SHA256SUMS
mkdir -p /work/source /outputs/wheelhouse /outputs/logs
cp /build-tools-freeze.txt /outputs/build-tools-freeze.txt
dpkg-query -W -f='${binary:Package}\t${Version}\n' > /outputs/build-dpkg-packages.tsv
rustc --version > /outputs/rust-version.txt
cargo --version >> /outputs/rust-version.txt
gcc --version > /outputs/gcc-version.txt
tar --extract --gzip --file /inputs/openssl-af1775b6.tar.gz --directory /work/source
openssl_src=$(find /work/source -mindepth 1 -maxdepth 1 -type d -name 'openssl-*')
test -n "$openssl_src"
cd "$openssl_src"
for patchfile in /inputs/patches/CVE-*.patch; do
    patch --batch --fuzz=0 -p1 --input "$patchfile"
done
./Configure linux-x86_64 no-shared no-module -fPIC -fstack-protector-strong -D_FORTIFY_SOURCE=3 -Wl,-Bsymbolic-functions -Wl,-z,relro -Wl,-z,now -Wl,-z,noexecstack --prefix=/opt/openssl-security --libdir=lib
cp configdata.pm /outputs/openssl-configdata.pm
make -j2 build_libs
make install_sw
/opt/openssl-security/bin/openssl version -a > /outputs/openssl-version.txt
find . -type f \( -name '*.c' -o -name '*.h' -o -name '*.in' \) -print0 | sort -z | xargs -0 sha256sum > /outputs/patched-openssl-source.sha256
cd /work/source
tar --extract --gzip --file /inputs/cryptography-50.0.2.tar.gz
cd cryptography-50.0.2
export OPENSSL_DIR=/opt/openssl-security OPENSSL_STATIC=1
export CARGO_HOME=/work/cargo
cp -a /cargo-input /work/cargo
export CARGO_NET_OFFLINE=true
# Cargo.lock is unchanged; --locked is required by the upstream maturin config.
python -m pip wheel --no-deps --no-build-isolation --wheel-dir /outputs/wheelhouse .
cp Cargo.lock /outputs/Cargo.lock
sha256sum /outputs/wheelhouse/*.whl > /outputs/wheelhouse.sha256
python -m pip install --no-deps /outputs/wheelhouse/*.whl
python -m pip check
python -c 'import cryptography,ssl; from cryptography.hazmat.backends.openssl.backend import backend; print(cryptography.__version__); print(backend.openssl_version_text()); print(ssl.OPENSSL_VERSION)' > /outputs/loaded-versions.txt
cargo test --all --locked --offline --release > /outputs/logs/rust-tests.log 2>&1
cp -a /opt/openssl-security /outputs/static-prefix
printf 'passed\n' > /outputs/build-and-tests.status
