# Zippyshare Uploader (Special for videos)

Installation:
```sh
git clone https://github.com/lem0n4de/zippyshare-video-uploader
cd zippyshare-video-uploader
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python upload.py --help
```

Usage:
```
usage: upload.py [-h] [--file [FILE ...]] [--directory [DIRECTORY ...]] [--split] [--output OUTPUT] [--retries {0,1,2,3,4,5}] [--proxy PROXY]

options:
  -h, --help            show this help message and exit
  --file [FILE ...], -f [FILE ...]
                        Files to upload
  --directory [DIRECTORY ...], -d [DIRECTORY ...]
                        Directories containing files to upload
  --split, -s           Whether to zip files or not
  --output OUTPUT, -o OUTPUT
                        Output file
  --retries {0,1,2,3,4,5}, -r {0,1,2,3,4,5}
                        How many times to re-attempt failed uploads
  --proxy PROXY         HTTPS proxy. <IP>:<PORT>
```

Examples:
``` python
python upload.py -d directory
python upload.py -f file.mp4 -s
```