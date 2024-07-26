from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
import subprocess
import threading
import logging
import os

# Create Flask app and SocketIO instance
app = Flask(__name__)
socketio = SocketIO(app)

# Dictionary to store running processes
processes = {}

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(message)s')

@app.route('/')
def index():
    """Render the main page."""
    return render_template('index.html')

def read_stream(process, stream, sid, response_type, first_line):
    """Read from a stream and emit messages with the output."""
    buffer = ""
    for line in iter(stream.readline, ''):
        if line:
            buffer += line
            if line.endswith("\n"):
                if first_line:
                    first_line = False
                else:
                    logging.debug(f"Output for session {sid}: {buffer.strip()} : {len(buffer)}")
                    if buffer.strip():  # Check if the buffer is not empty
                        socketio.emit('command_response', {'response': buffer.strip(), 'response_type': response_type}, to=sid)
                    buffer = ""
    stream.close()

def read_output(process, sid):
    """Read output from a process and emit it to a client."""
    first_line = True
    try:
        # Start threads to read from the process's stdout and stderr
        thread_out = threading.Thread(target=read_stream, args=(process, process.stdout, sid, 'normal', first_line))
        thread_err = threading.Thread(target=read_stream, args=(process, process.stderr, sid, 'error', first_line))
        thread_out.start()
        thread_err.start()

        # Wait for both threads to finish
        thread_out.join()
        thread_err.join()

        # Wait for the process to finish and get the return code
        return_code = process.wait()
        if return_code != 0:
            logging.error(f"Process for session {sid} exited with error code {return_code}")
            socketio.emit('command_response', {'response': 'Command not found.', 'response_type': 'error'}, to=sid)

        logging.info(f"Process for session {sid} completed")
    except Exception as e:
        logging.error(f"Error reading output for session {sid}: {e}")
        socketio.emit('command_response', {'response': str(e), 'response_type': 'error'}, to=sid)
    finally:
        # Remove the process from the dictionary of running processes
        if sid in processes:
            del processes[sid]

@socketio.on('start_command')
def start_command(data):
    """Start a new command and read its output."""
    command = data['command']
    sid = request.sid
    logging.info(f"Starting command: {command} for session: {sid}")

    # Error Handling: Add more specific error handling
    if not command:
        logging.error(f"No command provided for session {sid}")
        emit('command_response', {'response': 'No command provided.', 'response_type': 'error'}, to=sid)
        return

    # Input Validation: Validate the command to prevent command injection attacks
    if ';' in command or '&&' in command:
        logging.error(f"Invalid command for session {sid}")
        emit('command_response', {'response': 'Invalid command.', 'response_type': 'error'}, to=sid)
        return

    # Kill existing process if present
    if sid in processes:
        processes[sid].kill()
        del processes[sid]

    try:
        # Detect the operating system and use the appropriate shell
        shell = 'cmd' if os.name == 'nt' else '/bin/bash'
        process = subprocess.Popen(
            shell, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE, text=True
        )
        processes[sid] = process

        join_room(sid)
        thread = threading.Thread(target=read_output, args=(process, sid))
        thread.start()

        # Send initial command to the subprocess
        process.stdin.write(command + "\n")
        process.stdin.flush()
    except Exception as e:
        logging.error(f"Error starting command for session {sid}: {e}")
        emit('command_response', {'response': str(e), 'response_type': 'error'}, to=sid)

@socketio.on('send_input')
def send_input(data):
    """Send input to a running command."""
    sid = request.sid
    input_text = data['input']
    logging.info(f"Received input for session {sid}: {input_text}")
    process = processes.get(sid)
    if process:
        try:
            process.stdin.write(input_text + "\n")
            process.stdin.flush()
            logging.debug(f"Input sent to process for session {sid}")
        except Exception as e:
            logging.error(f"Error sending input for session {sid}: {e}")
            emit('command_response', {'response': str(e), 'response_type': 'error'}, to=sid)
    else:
        logging.warning(f"No process found for session {sid} when trying to send input")
        emit('command_response', {'response': 'No process found.', 'response_type': 'error'}, to=sid)

@socketio.on('disconnect')
def on_disconnect():
    """Handle a client disconnecting."""
    sid = request.sid
    process = processes.get(sid)
    if process:
        process.kill()
        del processes[sid]
        logging.info(f"Process for session {sid} killed on disconnect")

if __name__ == '__main__':
    socketio.run(app, debug=True)
