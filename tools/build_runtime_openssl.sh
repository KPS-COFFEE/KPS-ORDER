#!/bin/sh
# Install only authenticated, released shared libraries and provider modules.
set -eu
version=3.5.10
archive=openssl-$version.tar.gz
release=https://github.com/openssl/openssl/releases/download/openssl-$version
signer=B146647E45A7B33947AB226B2A2C87D161692D40
build_dir=$(mktemp -d)
trap 'rm -rf "$build_dir"' EXIT
cd "$build_dir"
export GNUPGHOME="$build_dir/gnupg"
mkdir -m 700 "$GNUPGHOME"

curl --fail --location --retry 2 --output public-keys.asc https://openssl-library.org/source/pubkeys.asc
gpg --batch --import public-keys.asc
gpg --batch --export "$signer" > release-key.gpg
test -s release-key.gpg
curl --fail --location --retry 2 --output "$archive" "$release/$archive"
curl --fail --location --retry 2 --output "$archive.asc" "$release/$archive.asc"
gpgv --keyring "$build_dir/release-key.gpg" "$archive.asc" "$archive"
tar -xzf "$archive"
cd "openssl-$version"
./Configure shared --prefix=/usr/local --libdir=lib --openssldir=/etc/ssl
make -j4 build_sw
make -j4 test TESTS="test_verify test_evp test_ssl_new"
make DESTDIR=/openssl-runtime install_runtime install_modules install_engines
# Ship the matching config alongside the distro trust store and the upstream license.
install -D -m 644 apps/openssl.cnf /openssl-runtime/etc/ssl/openssl.cnf
doc=/openssl-runtime/usr/local/share/doc/openssl
install -D -m 644 LICENSE.txt "$doc/LICENSE.txt"
cd "$build_dir"
sha256sum "$archive" > "$doc/source.sha256"
cp "$archive.asc" "$doc/"
printf '%s\n' "$release/$archive" "$signer" > "$doc/release-origin.txt"
