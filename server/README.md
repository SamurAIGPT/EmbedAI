# privateGPT Server

A Flask application to manage your privateGPT instance.

## MacOS Installation Notes

Ensure you run:

```sh
pip install urllib3==1.26.6
```

To fix the lingering OpenSSL issue.

Now, you can execute:

```sh
python privateGPT.py
```

The server should be running on Port 5000.

If port 5000 is in use, on your Mac go to System Settings > Airdrop and Handoff
and disable the Airplay Receiver as it runs on port 5000.