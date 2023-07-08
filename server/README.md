# privateGPT Server

A Flask application to manage your privateGPT instance.

## Linux Installation Notes

If you get a cartopy build error, ensure you have that installed:
````sh
sudo apt-get install libgeos-dev 
````
## MacOS Installation Notes

If you get a cartopy build error, ensure you have that installed:
````sh
brew install libgeos-dev 
````

To fix the lingering OpenSSL issue, ensure you run:

```sh
pip install urllib3==1.26.6
```

Now, you can execute:

```sh
python privateGPT.py
```

The server should be running on Port 5000.

If port 5000 is in use, on your Mac go to System Settings > Airdrop and Handoff
and disable the Airplay Receiver as it runs on port 5000.