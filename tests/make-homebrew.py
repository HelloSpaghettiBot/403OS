#!/usr/bin/env python3
"""Build our original, freely redistributable NES input/SRAM acceptance cartridge.

Mapper 0. Draws an original checkerboard. A changes the palette and increments
battery-backed RAM at $6000, allowing the frontend's real input/save path to be
checked without distributing a commercial game. Source and output: MIT.
"""
import argparse
from pathlib import Path
import struct

def cartridge():
    program=bytearray();labels={};branches=[];jumps=[]
    def emit(*values):program.extend(values)
    def label(name):labels[name]=len(program)+0x8000
    def branch(op,name):emit(op,0);branches.append((len(program)-1,name))
    def jump(name):emit(0x4c,0,0);jumps.append((len(program)-2,name))
    def absolute(op,address):emit(op,address&255,address>>8)
    label('reset');emit(0x78,0xd8,0xa2,0xff,0x9a,0xa9,0)
    absolute(0x8d,0x2000);absolute(0x8d,0x2001)
    for name in ('vblank1','vblank2'):
        label(name);absolute(0x2c,0x2002);branch(0x10,name)
    # Palette, with persistent background color from SRAM.
    emit(0xa9,0x3f);absolute(0x8d,0x2006);emit(0xa9,0);absolute(0x8d,0x2006)
    absolute(0xad,0x6000);emit(0x29,0x3f);absolute(0x8d,0x2007)
    for value in (0x21,0x11,0x30):emit(0xa9,value);absolute(0x8d,0x2007)
    # Blank name table selects the checkerboard in CHR tile zero.
    emit(0xa9,0x20);absolute(0x8d,0x2006);emit(0xa9,0);absolute(0x8d,0x2006)
    emit(0xa0,4,0xa2,0);label('clear');absolute(0x8d,0x2007);emit(0xe8);branch(0xd0,'clear');emit(0x88);branch(0xd0,'clear')
    emit(0xa9,0);absolute(0x8d,0x2005);absolute(0x8d,0x2005)
    emit(0xa9,0x0a);absolute(0x8d,0x2001)
    label('frame');absolute(0x2c,0x2002);branch(0x10,'frame')
    emit(0xa9,1);absolute(0x8d,0x4016);emit(0xa9,0);absolute(0x8d,0x4016)
    absolute(0xad,0x4016);emit(0x29,1);branch(0xf0,'frame')
    absolute(0xee,0x6000)
    emit(0xa9,0x3f);absolute(0x8d,0x2006);emit(0xa9,0);absolute(0x8d,0x2006)
    absolute(0xad,0x6000);emit(0x29,0x3f);absolute(0x8d,0x2007)
    emit(0xa9,0);absolute(0x8d,0x2005);absolute(0x8d,0x2005);jump('frame')
    for offset,name in branches:
        distance=labels[name]-(0x8000+offset+1)
        if not -128<=distance<=127:raise ValueError('6502 branch too far')
        program[offset]=distance&255
    for offset,name in jumps:program[offset:offset+2]=struct.pack('<H',labels[name])
    prg=program+bytes(16384-len(program)-6)+struct.pack('<HHH',labels['reset'],labels['reset'],labels['reset'])
    chr_data=bytes([0xaa,0x55]*4+[0]*8)+bytes(8192-16)
    return b'NES\x1a'+bytes([1,1,2,0,1])+bytes(7)+prg+chr_data

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_bytes(cartridge())
