# Custom OpenSSL security backport

This bundle replaces only `/usr/lib/libcrypto.so.4` and `/usr/lib/libssl.so.4` in
the Personal Wolfi runtime. It is an explicitly custom build, not a signed
Chainguard replacement or a claim of binary equivalence with its packages.
Retain the original APK database and the separate installed build manifest.

The source is upstream OpenSSL 4.0.3 commit
`af1775b60dfa141a4ad762585052cabeb9f37e9e`, authenticated through its release tag
and the official OpenSSL signing certificate. The build retains the public
Wolfi recipe's X.509 cherry-pick, all six hardening patches and configuration,
then adds the five official OpenSSL fixes recorded in `build-manifest.json`.
The source archive, patches and Apache licenses are included here.

`payload.tar.gz` has exactly two regular files with fixed paths and mode 0444.
`install_payload.py --root /runtime` checks every source input and payload hash,
both original r8 library hashes, archive shape and all destination parents
before writing either library. It rejects unknown vendor baselines rather than
overwriting a newer or unrelated package. It streams hashes and tar headers
under fixed size limits. The original package records are not rewritten.

The final stripped libraries preserve every allocated ELF section from the
tested build. Independent clean builds with the recorded immutable toolchain,
fixed `/build/openssl` source path and `SOURCE_DATE_EPOCH=1794528477` reproduced
both raw libraries and the test-only CLI byte for byte. `rebuild.py` reproduces
the reviewed final library hashes without network access. `Dockerfile.rebuild`
records every toolchain package version; repository availability must be
reverified when rebuilding that toolchain. The exact local builder identity is
also preserved in the manifest.

Final library checks passed: 24 normal TLS/GCM-SIV assertions in the actual
application image, six CLI commands, five capability outputs exactly matching
the signed r8 baseline, and exact exported ABI/NEEDED-library parity with ELF
hardening retained. The new EC invalid-order regression, ordinary BIO datagram
and QUIC stream tests passed. See `provenance/` for immutable proof hashes and
the separate complete raw evidence under the task directory.

The full hardened upstream suite does **not** pass: 83 of 368 recipes fail,
including bounded unsupported-fixture loops. All 83 failure identities were
compared with the old-code hardened control; no additional candidate failures
were observed. The control uses the actual signed r8 CLI and shared libraries
for dynamically linked cases, and the same hardening/toolchain with only the
five fixes reversed for statically linked internal tests. The new invalid-order
boundary is absent from that old-code control. One PEM recipe's numeric ordering
differs because it iterates an unsorted Perl hash; both variants reject the same
two DSA fixtures, verified separately using the actual image and final bytes.
These failures and timeouts remain failures, not an expected-pass allowlist.

A separate **test-only causal control** reversed just the cryptographic
hardening and prechosen TLS-group patches, retaining all five CVE fixes.
All 83 previously failing recipes and 1,948 assertions then passed, including
the previously busy-looping SSL fixtures. This establishes the existing
hardening policy as their cause. That control's libraries were never exported
or included in this bundle; all six vendor patches remain in the shipped bytes.

The final fixed-manifest installer passed 18 isolated positive and negative
checks, including tampered second members and baselines, duplicate/missing/extra
archive members, link/path escapes, size limits and provenance-directory
symlinks. Every rejected case left both staged libraries unchanged. The test
receipt is recorded separately from the manifest inputs to avoid a self-hash
cycle; its exact manifest and installer hashes are included in that receipt.

This bundle does not by itself approve a production release. The containing
application requires its full image inventory, native loaded-file verification,
application and database-role regressions, preview deployment and authenticated
actual production runtime evidence.
