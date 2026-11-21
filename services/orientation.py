"""IIO accelerometer sampling and live Weston output rotation (MIT).

Sensor axes/scales follow Linux Documentation/ABI/testing/sysfs-bus-iio.
Mount matrices follow that ABI and systemd's ACCEL_MOUNT_MATRIX convention.
No sensor readings, identity guesses or compositor restarts are synthesized.
"""
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import time

IIO_ROOT=Path('/sys/bus/iio/devices')
UDEV_ROOT=Path('/run/udev/data')
SOCKET=Path('/run/surface/display/rotation.sock')
HELPER='/usr/sbin/surface-display'
MODULE=Path('/usr/lib/weston/surface-rotation.so')
TRANSFORMS={'normal':'normal','left-up':'90','bottom-up':'180','right-up':'270'}
IDENTITY=((1.,0.,0.),(0.,1.,0.),(0.,0.,1.))


def bounded_text(path,limit=512):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(fd,'r') as stream:
        text=stream.read(limit+1)
    if len(text)>limit:raise ValueError('Sensor attribute is oversized')
    return text.strip()


def number(text):
    value=float(text)
    if not math.isfinite(value) or abs(value)>1e12:raise ValueError('Invalid accelerometer value')
    return value


def parse_matrix(text):
    rows=text.split(';')
    if len(rows)!=3:raise ValueError('Invalid accelerometer mount matrix')
    matrix=tuple(tuple(number(v.strip()) for v in row.split(',')) for row in rows)
    if any(len(row)!=3 for row in matrix):raise ValueError('Invalid accelerometer mount matrix')
    # Reflections are valid in existing sensor hwdb records; require an
    # orthonormal basis, not a positive determinant or integer-only entries.
    for i in range(3):
        for j in range(3):
            dot=sum(matrix[i][k]*matrix[j][k] for k in range(3))
            if abs(dot-(1 if i==j else 0))>.03:raise ValueError('Non-orthogonal accelerometer mount matrix')
    return matrix


def udev_properties(node,udev_root):
    try:
        device=bounded_text(node/'dev')
        if not re.fullmatch(r'[0-9]+:[0-9]+',device):return {}
        raw=bounded_text(udev_root/('c'+device),65536)
    except FileNotFoundError:return {}
    values={}
    for line in raw.splitlines():
        if line.startswith('E:ACCEL_MOUNT_MATRIX='):values['matrix']=line.split('=',1)[1]
        if line.startswith('E:ACCEL_LOCATION='):values['location']=line.split('=',1)[1]
    return values


def discover(iio_root=IIO_ROOT,udev_root=UDEV_ROOT):
    candidates=[]
    for node in sorted(iio_root.glob('iio:device*'))[:32]:
        if not re.fullmatch(r'iio:device[0-9]+',node.name):continue
        if not all((node/f'in_accel_{axis}_raw').is_file() for axis in 'xyz'):continue
        props=udev_properties(node,udev_root)
        try:label=bounded_text(node/'label')
        except FileNotFoundError:label=''
        if props.get('location')=='base' or label in ('accel-base','accel-camera'):continue
        candidates.append((0 if label=='accel-display' or props.get('location')=='display' else 1,node,props))
    if not candidates:raise ValueError('No supported IIO display accelerometer is available')
    rank=min(item[0] for item in candidates);best=[item for item in candidates if item[0]==rank]
    if len(best)!=1:raise ValueError('Multiple accelerometers need a display location before rotation can be enabled')
    _,node,props=best[0]
    matrix=IDENTITY;source='identity (no firmware or udev mount matrix)'
    if 'matrix' in props:matrix=parse_matrix(props['matrix']);source='udev ACCEL_MOUNT_MATRIX'
    else:
        for name in ('in_accel_mount_matrix','mount_matrix','in_mount_matrix'):
            try:raw=bounded_text(node/name)
            except FileNotFoundError:continue
            matrix=parse_matrix(raw);source=name;break
    try:name=bounded_text(node/'name')
    except FileNotFoundError:name=node.name
    return node,matrix,source,name


def sample(iio_root=IIO_ROOT,udev_root=UDEV_ROOT):
    node,matrix,source,name=discover(iio_root,udev_root)
    axes=[]
    for axis in 'xyz':
        raw=number(bounded_text(node/f'in_accel_{axis}_raw'))
        try:scale=number(bounded_text(node/f'in_accel_{axis}_scale'))
        except FileNotFoundError:scale=number(bounded_text(node/'in_accel_scale'))
        if scale<=0:raise ValueError('Accelerometer scale must be positive')
        try:offset=number(bounded_text(node/f'in_accel_{axis}_offset'))
        except FileNotFoundError:
            try:offset=number(bounded_text(node/'in_accel_offset'))
            except FileNotFoundError:offset=0.
        axes.append((raw+offset)*scale)
    vector=[sum(row[k]*axes[k] for k in range(3)) for row in matrix]
    return dict(available=True,sensor=node.name,sensor_name=name,matrix_source=source,vector=vector)


def monitor():
    while True:
        try:value=sample()
        except (OSError,ValueError) as error:value=dict(available=False,message=str(error))
        print(json.dumps(value,ensure_ascii=True),flush=True)
        time.sleep(.5 if value['available'] else 5.)


class SensorWorker:
    """At most one child, bounded output, no synchronous sysfs reads in HOME's supervisor."""
    def __init__(self):
        self.process=None;self.buffer=b'';self.last=0.;self.retry=0.;self.stopping=False
    def stop(self):
        if self.process is None or self.stopping:return
        if self.process.poll() is None:
            try:os.killpg(self.process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        self.stopping=True
        if self.process.stdout:self.process.stdout.close()
    def poll(self,now):
        if self.process is not None and self.process.poll() is not None:
            if self.process.stdout:self.process.stdout.close()
            self.process=None;self.stopping=False;self.retry=max(self.retry,now+5)
            return dict(available=False,message='Accelerometer worker stopped; rotation remains unchanged')
        if self.stopping:return None
        if self.process is None:
            if now<self.retry:return None
            self.process=subprocess.Popen([HELPER,'--rotation-monitor'],stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
            os.set_blocking(self.process.stdout.fileno(),False)
            self.buffer=b'';self.last=now
        try:chunk=os.read(self.process.stdout.fileno(),8192)
        except BlockingIOError:chunk=b''
        self.buffer+=chunk
        if len(self.buffer)>16384:
            self.stop();self.retry=now+10
            return dict(available=False,message='Accelerometer worker exceeded its output bound')
        latest=None
        while b'\n' in self.buffer:
            line,self.buffer=self.buffer.split(b'\n',1)
            try:
                value=json.loads(line)
                if not isinstance(value,dict) or type(value.get('available')) is not bool:raise ValueError()
                if value['available']:
                    vector=value.get('vector')
                    if not isinstance(vector,list) or len(vector)!=3 or any(type(x) not in (float,int) or not math.isfinite(x) for x in vector):raise ValueError()
                latest=value;self.last=now
            except (ValueError,UnicodeError,OverflowError,TypeError):
                latest=dict(available=False,message='Accelerometer returned an invalid sample')
        # Missing-sensor reports arrive every 5s, so keep the watchdog above that.
        if now-self.last>7:
            self.stop();self.retry=now+10
            return dict(available=False,message='Accelerometer read timed out; rotation remains unchanged')
        return latest


class Debounce:
    def __init__(self):self.candidate=None;self.since=0.;self.count=0;self.last_change=-10.
    def reset(self):self.candidate=None;self.count=0
    def update(self,vector,current,now):
        x,y,z=vector;gravity=math.sqrt(x*x+y*y+z*z)
        # Reject movement/freefall and near-horizontal placement. Require a
        # clearly dominant in-plane axis; the deadband prevents diagonal chatter.
        if not 6.0<=gravity<=13.5 or math.hypot(x,y)<5.0 or abs(z)>gravity*.85:
            self.reset();return None
        if max(abs(x),abs(y))<min(abs(x),abs(y))*1.35:
            self.reset();return None
        candidate=('left-up' if x>0 else 'right-up') if abs(x)>abs(y) else ('normal' if y<0 else 'bottom-up')
        transform=TRANSFORMS[candidate]
        if transform==current:self.reset();return None
        if candidate!=self.candidate:self.candidate=candidate;self.since=now;self.count=1;return None
        self.count+=1
        if self.count>=3 and now-self.since>=1.2 and now-self.last_change>=3.:
            self.last_change=now;self.reset();return transform
        return None


def internal_output(outputs):
    names=[x['name'] for x in outputs if x['name'].startswith(('eDP-','DSI-','LVDS-'))]
    return names[0] if len(names)==1 else ''


def apply_transform(output,transform,path=SOCKET):
    if not re.fullmatch(r'(?:eDP|DSI|LVDS)-[A-Za-z0-9-]{1,58}',output) or transform not in TRANSFORMS.values():
        raise ValueError('Invalid internal display rotation')
    info=path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077:
        raise ValueError('Weston rotation socket is not private to its owner')
    with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as client:
        client.settimeout(.4);client.connect(str(path))
        peer=client.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12)
        import struct
        if struct.unpack('3i',peer)[1]!=os.geteuid():raise ValueError('Weston rotation peer identity does not match')
        client.sendall(f'{output} {transform}'.encode('ascii'))
        reply=client.recv(256)
    if reply!=b'OK':raise ValueError('Weston rotation failed: '+reply.decode('ascii',errors='replace')[:160])
