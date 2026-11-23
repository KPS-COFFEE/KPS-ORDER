#!/bin/sh
# Install only authenticated, released shared libraries and provider modules.
set -eu
version=3.5.9
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
gpg --batch --no-default-keyring --keyring "$build_dir/release-key.gpg" --verify "$archive.asc" "$archive"
printf '%s  %s\n' 603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a "$archive" | sha256sum -c -
tar -xzf "$archive"
cd "openssl-$version"
# Apply published upstream 3.5 maintenance fixes to the signed release.
cat > "$build_dir/backports.txt" <<'PATCHES'
CVE-2026-107494 734c6cd7444bdbf589bfec917e47598c1b21ddd3 c1726a50d589b2dc289727865c184382c2bd846733ddb139427062713ebeb5bd
CVE-2026-35190 9491b0d9cc7950af1f3dc89499bd5433b9cd4f43 e1b45282a8876fdda6b930273e28bad1cdb1c6915ccd25fe0b61789281489ed1
CVE-2026-107493 c8f6d04f8e6d40b1bf83334e4a2ec9daf5943a3d 6176d694ac38251bfa4e11198d002d8720f9efb05deffb35703adb93be6173e1
CVE-2026-124709 f3acb0a3328046741479482c51cec815839e1b05 d52a6af23bc966daaa9eded816a392d429a67bb76faafd922f4c4cf35a9349f4
PATCHES
while read -r issue commit checksum; do
    file="$build_dir/$issue.patch"
    curl --fail --location --retry 2 --output "$file" "https://github.com/openssl/openssl/commit/$commit.patch"
    printf '%s  %s\n' "$checksum" "$file" | sha256sum -c -
    patch --batch --forward --fuzz=0 -p1 < "$file"
done < "$build_dir/backports.txt"
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
cp "$archive.asc" backports.txt "$doc/"
printf '%s\n' "$release/$archive" "$signer" > "$doc/release-origin.txt"
