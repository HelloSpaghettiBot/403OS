#!/usr/bin/env python3
"""Install a minimal official LLVM distribution, preserving real tools/libraries.

Omitted executable targets are removed from CMake exports. This keeps exported
metadata honest without retaining >5 GiB of unrelated LLVM utility executables.
No host C/C++ runtime is installed or replaced. All retained binaries are the
unmodified, checksum-pinned upstream release binaries.
"""
import argparse
import json
import pathlib
import re
import shutil
import tarfile

SHA256='df0e1ecf16caf3489a272a5eea4eec9b0d82878f6477fa309504f918a0006384'
TOOLS={'clang','clang++','clang-22','clang-cpp','llvm-config','llvm-as','llvm-dis',
       'llvm-link','llvm-tblgen','llvm-ar','llvm-ranlib','llvm-objcopy','llvm-strip',
       'llc','opt'}

def selected(name):
    path=pathlib.PurePosixPath(name)
    return (name.startswith(('include/llvm','include/clang','include/polly',
                            'lib/cmake/llvm/','lib/cmake/clang/','lib/clang/22/include/'))
            or (len(path.parts)==2 and path.parts[0]=='lib' and path.name.startswith(
                ('libLLVM','libclang','libPolly','libRemarks','libLTO','LLVMgold','LLVMPolly','BugpointPasses')))
            or (path.parts[0]=='bin' and path.name in TOOLS))

def trim_exports(root):
    removed=[]
    for directory,base in [('llvm','LLVMExports'),('clang','ClangTargets')]:
        folder=root/'lib/cmake'/directory
        omitted=set()
        for configuration in folder.glob(base+'-*.cmake'):
            text=configuration.read_text()
            pattern=r'(?ms)^# Import target "([^"]+)"[^\n]*\n.*?(?=^# Import target |^# Commands beyond|\Z)'
            def block(match):
                files=re.findall(r'\$\{_IMPORT_PREFIX\}/([^";]+)',match.group())
                if any(not (root/name).exists() for name in files):
                    omitted.add(match.group(1));return ''
                return match.group()
            configuration.write_text(re.sub(pattern,block,text))
        main=folder/(base+'.cmake');text=main.read_text()
        pattern=r'(?ms)^# Create imported target ([^\n]+)\n.*?(?=^# Create imported target |^# Load information|\Z)'
        text=re.sub(pattern,lambda m:'' if m.group(1) in omitted else m.group(),text)
        def expected(match):
            return match.group(1)+' '.join(x for x in match.group(2).split() if x not in omitted)+')'
        text=re.sub(r'(foreach\(_cmake_expected_target IN ITEMS )([^\n]*)\)',expected,text)
        main.write_text(text);removed+=sorted(omitted)
    return removed

def prepare(archive,cache):
    marker=cache/'surface-minimal.json'
    if marker.exists() and json.loads(marker.read_text()).get('archive_sha256')==SHA256:
        return
    cache.mkdir(parents=True,exist_ok=True)
    # Reuse the fully selected initial audit extraction on the first build.
    if not (cache/'surface-manifest.json').exists():
        with tarfile.open(archive,'r|xz') as stream:
            for member in stream:
                path=pathlib.PurePosixPath(member.name)
                if len(path.parts)<2 or '..' in path.parts or path.is_absolute():continue
                relative=pathlib.PurePosixPath(*path.parts[1:]);name=str(relative)
                if not selected(name):continue
                if member.issym():
                    target=pathlib.PurePosixPath(member.linkname)
                    if target.is_absolute() or '..' in target.parts:raise RuntimeError('Unsafe upstream archive symlink')
                member.name=name
                # Explicit path/link validation supports the builder's Python3.11.
                stream.extract(member,cache)
    for path in (cache/'bin').iterdir():
        if path.name not in TOOLS:path.unlink()
    omitted=trim_exports(cache)
    for required in ('bin/clang-22','bin/llvm-config','lib/libLLVMCore.a','lib/libclangAST.a','include/llvm/IR/Module.h'):
        if not (cache/required).is_file():raise RuntimeError('Missing upstream development file: '+required)
    marker.write_text(json.dumps({'archive_sha256':SHA256,'version':'22.1.8','retained_tools':sorted(TOOLS),'omitted_cmake_targets':omitted},indent=2)+'\n')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('archive',type=pathlib.Path);parser.add_argument('cache',type=pathlib.Path);args=parser.parse_args();prepare(args.archive,args.cache)
