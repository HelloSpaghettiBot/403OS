#!/usr/bin/python3
"""Keep a real interactive BlueZ agent alive throughout pairing and connection.

The UI exchanges JSON lines with this helper. Raw client output and entered
credentials are never forwarded or saved. Unlike bluetoothctl's positional
command mode, its interactive mode exports the agent needed for authentication.
"""
import argparse
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time

ADDRESS = re.compile(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\Z')
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)')
CLIENT = '/usr/bin/bluetoothctl'
SERVICE = '/usr/sbin/surface-bluetooth'
MAX_RESPONSE = 2048


class PairingError(ValueError):
    def __init__(self, message, code='failed'):
        super().__init__(message)
        self.code = code


def json_event(event):
    print(json.dumps(event, ensure_ascii=True), flush=True)


class PairingSession:
    """Testable agent session. Only main() may invoke the guarded power action."""
    def __init__(self, client_argv=None, env=None, emit=json_event, response_fd=0,
                 timeouts=None):
        self.client_argv = list(client_argv or [CLIENT])
        self.env = dict(os.environ if env is None else env)
        self.emit = emit
        self.response_fd = response_fd
        self.timeouts = dict(overall=120, register=12, pair=60, trust=12,
                             connect=30, info=12, prompt=60)
        self.timeouts.update(timeouts or {})
        self.process = None
        self.selector = None
        self.buffer = b''
        self.responses = b''
        self.pending = None
        self.prompt_count = 0
        self.last_display = None

    def event(self, event, message, **fields):
        self.emit(dict(event=event, message=message, **fields))

    def send(self, command):
        try:
            self.process.stdin.write(command.encode('utf-8') + b'\n')
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise PairingError('The Bluetooth agent stopped responding', 'client-exited') from error

    def phase(self, name, message, command=None):
        self.state = name
        self.phase_deadline = time.monotonic() + self.timeouts[name]
        self.buffer = b''
        self.event('phase', message, phase=name)
        if command:
            self.send(command)

    def text(self):
        decoded = ANSI.sub('', self.buffer.decode('utf-8', errors='replace'))
        return ''.join(c for c in decoded if c in '\n\r\t' or ord(c) >= 32)

    def prompt(self, kind, message, code=None):
        if self.pending:
            return
        self.prompt_count += 1
        self.pending = dict(id=str(self.prompt_count), kind=kind,
                            deadline=time.monotonic() + self.timeouts['prompt'])
        fields = dict(id=self.pending['id'], kind=kind)
        if code is not None:
            fields['code'] = code
        self.event('prompt', message, **fields)

    def agent_output(self, text):
        # Prompts have no trailing newline; match the actual BlueZ 5.86 strings.
        if self.pending:
            return
        if re.search(r'\[agent\]\s+Enter PIN code:', text):
            self.prompt('pin', 'Enter the PIN shown by the Bluetooth device')
        elif re.search(r'\[agent\]\s+Enter passkey \(number in 0-999999\):', text):
            self.prompt('passkey', 'Enter the passkey shown by the Bluetooth device')
        else:
            confirmation = re.search(r'\[agent\]\s+Confirm passkey (\d{6}) \(yes/no\):', text)
            if confirmation:
                code = confirmation.group(1)
                self.prompt('confirm', 'Does the device show passkey ' + code + '?', code)
            elif re.search(r'\[agent\]\s+(?:Accept pairing|Authorize service [0-9a-fA-F-]+) \(yes/no\):', text):
                self.prompt('confirm', 'Allow this Bluetooth device to pair or connect?')
        displays = re.findall(r'\[agent\]\s+(PIN code|Passkey): ([^\r\n]+)', text)
        if displays:
            kind, value = displays[-1]
            # Display callbacks intentionally show a code for entry on the peer.
            # Do not forward arbitrary client strings, history or user input.
            if re.fullmatch(r'[ -~]{1,16}', value) and (kind, value) != self.last_display:
                self.last_display = (kind, value)
                self.event('message', 'Enter ' + kind.lower() + ' ' + value + ' on the Bluetooth device',
                           phase=self.state)

    def response(self, line):
        try:
            answer = json.loads(line)
        except (ValueError, UnicodeError) as error:
            raise PairingError('The pairing response was invalid', 'invalid-response') from error
        if (not isinstance(answer, dict) or set(answer) != {'id', 'value'} or
                not self.pending or answer.get('id') != self.pending['id'] or
                not isinstance(answer.get('value'), str)):
            raise PairingError('The pairing response did not match the active request', 'invalid-response')
        value = answer['value']
        kind = self.pending['kind']
        if kind == 'confirm':
            if value not in ('yes', 'no'):
                raise PairingError('Choose Yes or No to confirm Bluetooth pairing', 'invalid-response')
        elif kind == 'passkey':
            if not re.fullmatch(r'[0-9]{1,6}', value) or int(value) > 999999:
                raise PairingError('Bluetooth passkeys must contain one to six digits', 'invalid-response')
        else:
            try:
                length = len(value.encode('utf-8'))
            except UnicodeError as error:
                raise PairingError('The Bluetooth PIN contained invalid text', 'invalid-response') from error
            if not 1 <= length <= 16 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise PairingError('Bluetooth PINs must contain 1 to 16 bytes without control characters', 'invalid-response')
        self.pending = None
        # Remove the answered prompt before readline redraws it. Responses are
        # never included in events or diagnostics.
        self.buffer = b''
        self.send(value)
        if kind == 'confirm' and value == 'no':
            raise PairingError('Bluetooth pairing was declined', 'rejected')
        self.event('phase', 'Waiting for the Bluetooth device', phase=self.state)

    def check_output(self, address, pair):
        text = self.text()
        if re.search(r'(?:^|[\r\n])(?:\[[^\]\r\n]{0,248}\][>#]\s*)?(?:Failed to (?:pair|connect|register agent)|Agent registration failed|Changing .* failed|No default controller available|Device .* not available|Request canceled|Agent released)', text):
            known = re.search(r'org\.bluez\.Error\.([A-Za-z]+)', text)
            suffix = known.group(1) if known else ''
            if self.state == 'pair' and suffix == 'AlreadyExists':
                self.phase('trust', 'Trusting the paired Bluetooth device', 'trust ' + address)
                return None
            if self.state == 'connect' and suffix == 'AlreadyConnected':
                self.phase('info', 'Checking the Bluetooth connection', 'info ' + address)
                return None
            messages = {
                'AuthenticationFailed': 'Bluetooth authentication failed. Put the device back into pairing mode and try again',
                'AuthenticationRejected': 'The Bluetooth device rejected pairing',
                'AuthenticationCanceled': 'Bluetooth pairing was canceled',
                'AuthenticationTimeout': 'The Bluetooth device did not answer the pairing request',
                'NotReady': 'The Bluetooth adapter is not ready',
                'ConnectionAttemptFailed': 'The Bluetooth device did not accept the connection',
                'NotAvailable': 'The Bluetooth device is no longer available. Scan again',
            }
            raise PairingError(messages.get(suffix, 'Bluetooth could not complete the ' + self.state + ' step'),
                               suffix or 'client-failed')
        if self.state == 'register' and re.search(r'(?:^|[\r\n])Agent registered(?:[\r\n]|$)', text):
            if pair:
                self.phase('pair', 'Pairing with the Bluetooth device', 'pair ' + address)
            else:
                self.phase('connect', 'Connecting the Bluetooth device', 'connect ' + address)
            return None
        self.agent_output(text)
        if self.state == 'pair' and re.search(r'(?:^|[\r\n])Pairing successful(?:[\r\n]|$)', text):
            self.phase('trust', 'Trusting the paired Bluetooth device', 'trust ' + address)
        elif self.state == 'trust' and re.search(r'(?:^|[\r\n])Changing ' + re.escape(address) + r' trust succeeded(?:[\r\n]|$)', text, re.I):
            self.phase('connect', 'Connecting the Bluetooth device', 'connect ' + address)
        elif self.state == 'connect' and re.search(r'(?:^|[\r\n])Connection successful(?:[\r\n]|$)', text):
            self.phase('info', 'Checking the Bluetooth connection', 'info ' + address)
        elif self.state == 'info':
            # A property-change notification alone cannot satisfy this check.
            # Only parse the output following this device's info header.
            header = re.search(r'(?:^|[\r\n])Device ' + re.escape(address) + r'(?:\s|$)', text, re.I)
            if header:
                values = {name.lower(): value == 'yes' for name, value in
                          re.findall(r'^\s*(Paired|Trusted|Connected): (yes|no)\s*$',
                                     text[header.end():], re.M)}
                required = ('paired', 'trusted', 'connected') if pair else ('connected',)
                if all(name in values for name in required):
                    if not all(values[name] for name in required):
                        raise PairingError('Bluetooth completed the request without a confirmed ' +
                                           ('paired, trusted connection' if pair else 'connection'), 'readback-failed')
                    return dict(paired=values.get('paired', False),
                                trusted=values.get('trusted', False), connected=values['connected'])
        return None

    def cleanup(self):
        if self.process is not None:
            try:
                # Signal the group even if its leader exited: a child must not
                # survive an unexpectedly terminated client.
                os.killpg(self.process.pid, signal.SIGTERM)
                if self.process.poll() is None:
                    try:
                        self.process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        os.killpg(self.process.pid, signal.SIGKILL)
                        self.process.wait(timeout=1)
            except ProcessLookupError:
                self.process.wait(timeout=1)
            finally:
                self.process.stdin.close()
                self.process.stdout.close()
        if self.selector is not None:
            self.selector.close()

    def run(self, address, pair=True):
        if not ADDRESS.fullmatch(address):
            raise PairingError('The Bluetooth address is invalid', 'invalid-address')
        address = address.upper()
        started = time.monotonic()
        try:
            # BlueZ readline can save history; this private ephemeral directory
            # prevents credentials from ever reaching a persistent history file.
            with tempfile.TemporaryDirectory(prefix='surface-bluetooth-agent-') as private_home:
                environment = dict(self.env, LC_ALL='C', LANG='C', TERM='dumb',
                                   HOME=private_home, XDG_CACHE_HOME=private_home)
                self.process = subprocess.Popen(self.client_argv + ['--agent', 'KeyboardDisplay'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    env=environment, start_new_session=True, bufsize=0)
                self.selector = selectors.PollSelector()
                self.selector.register(self.process.stdout, selectors.EVENT_READ, 'client')
                if self.response_fd is not None:
                    self.selector.register(self.response_fd, selectors.EVENT_READ, 'response')
                self.phase('register', 'Starting the Bluetooth pairing agent')
                while True:
                    now = time.monotonic()
                    if now - started >= self.timeouts['overall'] or now >= self.phase_deadline:
                        raise PairingError('Bluetooth timed out during the ' + self.state + ' step. Put the device in pairing mode and try again', 'timeout')
                    if self.pending and now >= self.pending['deadline']:
                        raise PairingError('The Bluetooth pairing question was not answered in time', 'timeout')
                    for key, _ in self.selector.select(timeout=0.1):
                        if key.data == 'client':
                            chunk = os.read(self.process.stdout.fileno(), 8192)
                            if not chunk:
                                raise PairingError('The Bluetooth pairing agent exited before completion', 'client-exited')
                            self.buffer = (self.buffer + chunk)[-65536:]
                            result = self.check_output(address, pair)
                            if result is not None:
                                self.event('complete', 'Bluetooth device connected', status='PASS', **result)
                                return result
                        else:
                            chunk = os.read(self.response_fd, MAX_RESPONSE + 1)
                            if not chunk:
                                self.selector.unregister(self.response_fd)
                                if self.pending:
                                    raise PairingError('The Bluetooth pairing question was closed', 'canceled')
                                continue
                            self.responses += chunk
                            if len(self.responses) > MAX_RESPONSE:
                                raise PairingError('The pairing response was too large', 'invalid-response')
                            while b'\n' in self.responses:
                                line, self.responses = self.responses.split(b'\n', 1)
                                self.response(line)
                    if self.process.poll() is not None:
                        # Drain any final output before reporting an exit.
                        chunk = os.read(self.process.stdout.fileno(), 8192)
                        if chunk:
                            self.buffer = (self.buffer + chunk)[-65536:]
                            result = self.check_output(address, pair)
                            if result is not None:
                                self.event('complete', 'Bluetooth device connected', status='PASS', **result)
                                return result
                        raise PairingError('The Bluetooth pairing agent exited before completion', 'client-exited')
        except PairingError as error:
            self.event('error', str(error), code=error.code)
            raise
        except OSError as error:
            failure = PairingError('The Bluetooth pairing agent could not be started or contacted', 'client-unavailable')
            self.event('error', str(failure), code=failure.code)
            raise failure from error
        finally:
            self.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--pair', metavar='ADDRESS')
    action.add_argument('--connect', metavar='ADDRESS')
    options = parser.parse_args()
    address = options.pair or options.connect
    if not ADDRESS.fullmatch(address):
        json_event(dict(event='error', message='The Bluetooth address is invalid', code='invalid-address'))
        return 1

    def canceled(_signum, _frame):
        raise PairingError('Bluetooth pairing was canceled', 'canceled')

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, canceled)
    try:
        # This is the only power action. The existing helper enforces root,
        # trusted boot marker, persistent DATA, radio and adapter readiness.
        json_event(dict(event='phase', message='Preparing the Bluetooth adapter', phase='power'))
        prepared = subprocess.run([SERVICE, '--power-on'], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  timeout=30, check=False, env=dict(os.environ, LC_ALL='C'))
        if prepared.returncode:
            raise PairingError('The Bluetooth adapter could not be prepared. Check Bluetooth diagnostics', 'adapter-not-ready')
        PairingSession().run(address, pair=bool(options.pair))
        return 0
    except (PairingError, OSError, subprocess.TimeoutExpired) as error:
        if not isinstance(error, PairingError):
            error = PairingError('The Bluetooth adapter did not answer the preparation request', 'adapter-not-ready')
        # Never include client output, MAC addresses, passcodes or entered input
        # in the diagnostic reason. The privileged helper collects radio logs.
        try:
            subprocess.run([SERVICE, '--record-error', 'pairing-session-' + error.code[:48]],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=12, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
        json_event(dict(event='error', message=str(error), code=error.code))
        return 1


if __name__ == '__main__':
    sys.exit(main())
