#!/bin/sh
set -eu
host_dir=$1
llvm_dir=$2
mkdir -p "$host_dir/bin" "$host_dir/lib/cmake" "$host_dir/include"
# Mirror only LLVM/Clang artifacts. Runtime libraries remain isolated/unmodified.
for source in "$llvm_dir"/bin/*; do
    name=${source##*/}
    test "$name" != llvm-config || continue
    test ! -e "$host_dir/bin/$name" || test -L "$host_dir/bin/$name"
    ln -sfn "../opt/${llvm_dir##*/}/bin/$name" "$host_dir/bin/$name"
done
# llvm-config records one Ubuntu build-host archive as an absolute path.
# Resolve that dependency through this isolated host prefix, where Buildroot
# built zstd, without changing the checksum-pinned upstream executable.
cat > "$host_dir/bin/llvm-config.new" <<'EOF'
#!/bin/sh
set -eu
prefix=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
result=$("$prefix/opt/surface-llvm-22.1.8/bin/llvm-config" "$@")
printf '%s\n' "$result" | sed 's|/usr/lib/x86_64-linux-gnu/libzstd.a|-lzstd|g'
EOF
chmod 0755 "$host_dir/bin/llvm-config.new"
mv -f "$host_dir/bin/llvm-config.new" "$host_dir/bin/llvm-config"
for source in "$llvm_dir"/lib/libLLVM* "$llvm_dir"/lib/libclang* "$llvm_dir"/lib/libPolly* "$llvm_dir"/lib/libRemarks* "$llvm_dir"/lib/libLTO*; do
    test -e "$source" || continue
    name=${source##*/}
    test ! -e "$host_dir/lib/$name" || test -L "$host_dir/lib/$name"
    ln -sfn "../opt/${llvm_dir##*/}/lib/$name" "$host_dir/lib/$name"
done
for name in llvm clang; do
    test ! -e "$host_dir/lib/cmake/$name" || test -L "$host_dir/lib/cmake/$name"
    ln -sfn "../../opt/${llvm_dir##*/}/lib/cmake/$name" "$host_dir/lib/cmake/$name"
done
for name in llvm llvm-c clang clang-c polly; do
    test -e "$llvm_dir/include/$name" || continue
    test ! -e "$host_dir/include/$name" || test -L "$host_dir/include/$name"
    ln -sfn "../opt/${llvm_dir##*/}/include/$name" "$host_dir/include/$name"
done
# Clang resource headers are queried relative to libdir by Mesa CLC.
mkdir -p "$host_dir/lib/clang"
ln -sfn "../../opt/${llvm_dir##*/}/lib/clang/22" "$host_dir/lib/clang/22"
