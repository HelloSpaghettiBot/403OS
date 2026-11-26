#!/usr/bin/env python3
"""Host-side iPega Bluetooth HID fixture for the disposable QEMU input test."""
import fcntl
import os
from pathlib import Path
import signal
import struct
import sys
import time

BUS_BLUETOOTH=5
VENDOR=0x1949
PRODUCT=0x0402
NAME=b'PG-9023S Keyboard'
EV_SYN=0
EV_KEY=1
EV_ABS=3
KEY_ESC=1
KEY_0=11
KEY_1=2
KEY_2=3
KEY_3=4
KEY_4=5
KEY_5=6
KEY_6=7
KEY_7=8
KEY_8=9
KEY_9=10
KEY_TAB=15
KEY_ENTER=28
KEY_W=17
KEY_A=30
KEY_S=31
KEY_D=32
KEY_B=48
KEY_C=46
KEY_E=18
KEY_F=33
KEY_G=34
KEY_H=35
KEY_I=23
KEY_J=36
KEY_K=37
KEY_L=38
KEY_M=50
KEY_N=49
KEY_O=24
KEY_P=25
KEY_Q=16
KEY_R=19
KEY_T=20
KEY_U=22
KEY_V=47
KEY_X=45
KEY_Y=21
KEY_Z=44
KEY_SPACE=57
BTN_SOUTH=0x130
BTN_EAST=0x131
BTN_C=0x132
BTN_NORTH=0x133
BTN_WEST=0x134
BTN_Z=0x135
BTN_TL=0x136
BTN_TR=0x137
BTN_TL2=0x138
BTN_TR2=0x139
BTN_SELECT=0x13a
BTN_START=0x13b
BTN_MODE=0x13c
BTN_THUMBL=0x13d
BTN_THUMBR=0x13e
BTN_DPAD_UP=0x220
BTN_DPAD_DOWN=0x221
BTN_DPAD_LEFT=0x222
BTN_DPAD_RIGHT=0x223
ABS_X=0
ABS_Y=1
ABS_Z=2
ABS_RX=3
ABS_RY=4
ABS_RZ=5
ABS_HAT0X=16
ABS_HAT0Y=17


def iowr(direction,kind,number,size):
    return (direction<<30)|(size<<16)|(kind<<8)|number


def iow(kind,number,size):return iowr(1,ord(kind),number,size)
def io(kind,number):return iowr(0,ord(kind),number,0)


UI_SET_EVBIT=iow('U',100,4)
UI_SET_KEYBIT=iow('U',101,4)
UI_SET_ABSBIT=iow('U',103,4)
UI_DEV_CREATE=io('U',1)
UI_DEV_DESTROY=io('U',2)
UI_DEV_SETUP=iow('U',3,92)
UI_ABS_SETUP=iow('U',4,28)

running=True


def stop(_signum,_frame):
    global running
    running=False


def setup(fd):
    fcntl.ioctl(fd,UI_SET_EVBIT,EV_KEY)
    fcntl.ioctl(fd,UI_SET_EVBIT,EV_ABS)
    keyboard=(*range(1,32),KEY_ESC,KEY_0,KEY_1,KEY_2,KEY_3,KEY_4,KEY_5,KEY_6,KEY_7,KEY_8,KEY_9,KEY_TAB,
              KEY_ENTER,KEY_SPACE,KEY_A,KEY_B,KEY_C,KEY_D,KEY_E,KEY_F,KEY_G,KEY_H,KEY_I,KEY_J,
              KEY_K,KEY_L,KEY_M,KEY_N,KEY_O,KEY_P,KEY_Q,KEY_R,KEY_S,KEY_T,KEY_U,KEY_V,KEY_W,
              KEY_X,KEY_Y,KEY_Z)
    for code in (*keyboard,BTN_SOUTH,BTN_EAST,BTN_C,BTN_NORTH,BTN_WEST,BTN_Z,
                 BTN_TL,BTN_TR,BTN_TL2,BTN_TR2,BTN_SELECT,BTN_START,BTN_MODE,BTN_THUMBL,BTN_THUMBR,
                 BTN_DPAD_UP,BTN_DPAD_DOWN,BTN_DPAD_LEFT,BTN_DPAD_RIGHT):
        fcntl.ioctl(fd,UI_SET_KEYBIT,code)
    for code,minimum,maximum in ((ABS_X,-32768,32767),(ABS_Y,-32768,32767),
                                 (ABS_Z,-32768,32767),(ABS_RZ,-32768,32767),
                                 (ABS_HAT0X,-1,1),(ABS_HAT0Y,-1,1)):
        fcntl.ioctl(fd,UI_SET_ABSBIT,code)
        fcntl.ioctl(fd,UI_ABS_SETUP,struct.pack('=H2xiiiiii',code,0,minimum,maximum,0,0,0))
    setup_data=struct.pack('=HHHH80sI',BUS_BLUETOOTH,VENDOR,PRODUCT,1,NAME,0)
    fcntl.ioctl(fd,UI_DEV_SETUP,setup_data)
    fcntl.ioctl(fd,UI_DEV_CREATE)


def event_path(fd,deadline):
    # Resolve only the device made by this descriptor, never a real controller
    # that happens to advertise the same name and product ID on the host.
    buffer=bytearray(128)
    fcntl.ioctl(fd,iowr(2,ord('U'),44,len(buffer)),buffer,True)
    sysname=bytes(buffer).split(b'\0',1)[0].decode('ascii')
    if not sysname.startswith('input') or not sysname[5:].isdigit():
        raise RuntimeError('uinput returned an invalid fixture identity')
    device=Path('/sys/devices/virtual/input')/sysname
    while time.monotonic()<deadline:
        for node in device.glob('event*'):
            base=node/'device'
            try:
                vendor=(base/'id/vendor').read_text().strip().lower().zfill(4)
                product=(base/'id/product').read_text().strip().lower().zfill(4)
                name=(base/'name').read_text().strip()
                if vendor=='1949' and product=='0402' and name==NAME.decode():
                    return '/dev/input/'+node.name
            except OSError:continue
        time.sleep(.05)
    raise RuntimeError('Host uinput did not create its Bluetooth iPega event node')


def emit(fd,code,value):
    now=time.time_ns();seconds,micros=divmod(now,1_000_000_000)
    record=struct.pack('@llHHi',seconds,micros//1000,EV_KEY,code,value)
    sync=struct.pack('@llHHi',seconds,micros//1000,EV_SYN,0,0)
    os.write(fd,record+sync)


def main():
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    fd=os.open('/dev/uinput',os.O_WRONLY|os.O_NONBLOCK|os.O_CLOEXEC)
    created=False
    try:
        setup(fd);created=True
        path=event_path(fd,time.monotonic()+5)
        print(path,flush=True)
        # QEMU grabs this input; generate no host/guest input until requested.
        for line in sys.stdin:
            if not running:break
            kind,code,value=line.split()
            if kind not in ('key','abs'):raise ValueError('Unknown input fixture command')
            code,value=int(code),int(value)
            if kind=='key':emit(fd,code,value)
            else:
                os.write(fd,struct.pack('@llHHi',0,0,EV_ABS,code,value)+struct.pack('@llHHi',0,0,EV_SYN,0,0))
    finally:
        if created:
            try:fcntl.ioctl(fd,UI_DEV_DESTROY)
            except OSError:pass
        os.close(fd)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('fake-iPega:',error,file=sys.stderr,flush=True)
        raise SystemExit(1)
