#!/bin/bash

# This script sets up the necessary directory and files for the flowgraph.

# Create a directory named 'radiodata' if it doesn't exist
mkdir -p radiodata

# Change directory to 'radiodata'
cd radiodata/

# Record 5 seconds of silence into a WAV file named 'doorbell2.wav'.
# The system's default input is typically silent if no mic is active or present.
arecord -f cd -d 5 -t wav doorbell2.wav

# Record 5 seconds of silence with one channel (mono), format S16_LE (16 bit Little Endian), 
# and a sample rate of 96000 Hz into a file named 'rx.wav'.
arecord -c 1 -f S16_LE -r 96000 -d 5 -t wav rx.wav

# Create a file named 'rxfirfile.iq' and fill it with zeros until it reaches 50 MB.
dd if=/dev/zero of=rxfirfile.iq bs=1M count=50

# Create another file named 'rxiqfile.iq' and fill it with zeros until it reaches 50 MB.
dd if=/dev/zero of=rxiqfile.iq bs=1M count=50

# Print a completion message
echo "Setup complete. 'radiodata' directory and the required files have been created."

# End of the script

