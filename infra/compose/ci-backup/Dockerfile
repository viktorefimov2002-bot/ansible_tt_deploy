# CI only: registry pulls were denied and official binary downloads returned 410.
# Official release sources, checksummed archives, fixed compiler/runtime digests.
FROM golang:1.24.2-alpine3.21@sha256:7772cb5322baa875edd74705556d08f0eeca7b9c4b5367754ce3f2f00041ccee AS compiler
ENV GOTOOLCHAIN=local CGO_ENABLED=0
WORKDIR /source

FROM compiler AS minio-build
# RELEASE.2025-04-22T22-12-26Z, annotated upstream tag resolved to this commit.
RUN wget -q https://codeload.github.com/minio/minio/tar.gz/0d7408fc9969caf07de6a8c3a84f9fbb10a6739e -O /tmp/source.tar.gz \
    && echo '7eb30a913fea30f18069abf194e1e78e4983b558cc526911ae1c11396a9859a5  /tmp/source.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/source.tar.gz --strip-components=1 \
    && go mod download \
    && go build -trimpath -buildvcs=false \
       -ldflags '-s -w -X github.com/minio/minio/cmd.Version=2025-04-22T22:12:26Z -X github.com/minio/minio/cmd.ReleaseTag=RELEASE.2025-04-22T22-12-26Z -X github.com/minio/minio/cmd.CommitID=0d7408fc9969caf07de6a8c3a84f9fbb10a6739e -X github.com/minio/minio/cmd.ShortCommitID=0d7408fc9969 -X github.com/minio/minio/cmd.CopyrightYear=2025' \
       -o /out/minio .

FROM compiler AS mc-build
# RELEASE.2025-04-16T18-13-26Z, annotated upstream tag resolved to this commit.
RUN wget -q https://codeload.github.com/minio/mc/tar.gz/b00526b153a31b36767991a4f5ce2cced435ee8e -O /tmp/source.tar.gz \
    && echo '4cd13e34daeeb8481c3ba8686b082f161b8dc1f7aad52d715a706a587349c6ae  /tmp/source.tar.gz' | sha256sum -c - \
    && tar -xzf /tmp/source.tar.gz --strip-components=1 \
    && go mod download \
    && go build -trimpath -buildvcs=false \
       -ldflags '-s -w -X github.com/minio/mc/cmd.Version=2025-04-16T18:13:26Z -X github.com/minio/mc/cmd.ReleaseTag=RELEASE.2025-04-16T18-13-26Z -X github.com/minio/mc/cmd.CommitID=b00526b153a31b36767991a4f5ce2cced435ee8e -X github.com/minio/mc/cmd.ShortCommitID=b00526b153a3 -X github.com/minio/mc/cmd.CopyrightYear=2025' \
       -o /out/mc .

FROM curlimages/curl:8.12.1@sha256:94e9e444bcba979c2ea12e27ae39bee4cd10bc7041a472c4727a558e213744e6 AS runtime
# Match the existing fixture's certificate/configuration mounts and writable volume.
USER root
ENV HOME=/root

FROM runtime AS minio
COPY --from=minio-build /out/minio /usr/bin/minio
COPY --from=minio-build /source/LICENSE /licenses/minio/LICENSE
ENTRYPOINT ["/usr/bin/minio"]

FROM runtime AS mc
COPY --from=mc-build /out/mc /usr/bin/mc
COPY --from=mc-build /source/LICENSE /licenses/mc/LICENSE
ENTRYPOINT ["/usr/bin/mc"]
