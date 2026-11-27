FROM docker.io/library/debian@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential bash bc binutils bzip2 ca-certificates cmake cpio file gawk \
    git libncurses-dev libssl-dev locales ninja-build patch perl pkg-config \
    python3 rsync sed tar unzip wget xz-utils mtools squashfs-tools xorriso grub-common && \
    rm -rf /var/lib/apt/lists/* && \
    localedef -i en_US -f UTF-8 en_US.UTF-8
ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
WORKDIR /work
