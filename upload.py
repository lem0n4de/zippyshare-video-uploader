import argparse
import re
import sys
import requests
import mimetypes
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from typing import List
from pathlib import Path
from requests_toolbelt import MultipartEncoder, MultipartEncoderMonitor
import subprocess
from datetime import timedelta


class ZPUploadException(Exception):
    pass


class FileIsTooBigException(ZPUploadException):
    pass


class Duration:
    weeks: int
    days: int
    hours: int
    minutes: int
    seconds: int

    def __init__(
        self,
        days=0,
        seconds=0,
        minutes=0,
        hours=0,
        weeks=0,
    ):
        self.weeks = int(weeks)
        self.days = int(days)
        self.hours = int(hours)
        self.minutes = int(minutes)
        self.seconds = int(seconds)

    def _build_duration(self):
        if self.seconds >= 60:
            self.minutes += int(self.seconds // 60)
            self.seconds = int(self.seconds % 60)

        if self.minutes >= 60:
            self.hours += int(self.minutes // 60)
            self.minutes = int(self.minutes % 60)

        if self.hours >= 24:
            self.days += int(self.hours // 24)
            self.hours = int(self.hours % 24)

        if self.days >= 7:
            self.weeks += int(self.days // 7)
            self.days = int(self.days % 7)

    def add_seconds(self, seconds):
        self.seconds += seconds
        self._build_duration()

    def add_minutes(self, minutes):
        self.minutes += minutes
        self._build_duration()

    def add_hours(self, hours):
        self.hours += hours
        self._build_duration()

    def add_days(self, days):
        self.days += days
        self._build_duration()

    def add_weeks(self, weeks):
        self.weeks += weeks
        self._build_duration()


class ZPUploader:
    file_list: List[Path]
    zip_files: bool
    split_videos: bool
    retries: int
    server_name: str
    session: requests.Session
    SIZE_LIMIT = 520000000
    executor: ThreadPoolExecutor

    def __init__(
        self, file_list: List[Path], split_videos=False, retries=0, proxy=None
    ):
        self.file_list = file_list
        self.split_videos = split_videos
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome"
                "/75.0.3770.100 Safari/537.36",
                "Referer": "https://www.zippyshare.com/",
            }
        )
        if proxy is not None:
            self.session.proxies.update({"https": "https://" + proxy})

    def get_server(self):
        r = self.session.get("https://www.zippyshare.com/")
        r.raise_for_status()
        match = re.search(r"var server = \'(www\d{1,3})\';", r.text)
        if not match:
            raise ZPUploadException("Failed to extract server number")
        return match.group(1)

    def check_size(self, path: Path) -> int:
        if path.stat().st_size > self.SIZE_LIMIT:
            if not self.split_videos or "video" not in mimetypes.guess_type(path)[0]:
                raise FileIsTooBigException(
                    f"{path.name} is bigger than 500MB. If you want this script to "
                    f"automatically zip it pass -s argument."
                )
        return path.stat().st_size

    @staticmethod
    def get_mime_type(path: Path):
        mt = mimetypes.guess_type(path)[0]
        if mt is None:
            mt = "application/octet-stream"
        return mt

    def upload_file(self, path: Path):
        server = self.get_server()
        size = path.stat().st_size
        url = f"https://{server}.zippyshare.com/upload"
        pb = tqdm(total=size, unit="B", unit_scale=True, file=sys.stdout, leave=False)
        pb.set_description(path.name)
        data = {
            "name": path.name,
            "file": (path.name, path.open("rb"), self.get_mime_type(path)),
        }
        multi = MultipartEncoder(fields=data)
        monitor = MultipartEncoderMonitor(
            multi, lambda m: pb.update(monitor.bytes_read - pb.n)
        )
        self.session.headers.update({"Content-Type": monitor.content_type})
        r = self.session.post(url, data=monitor)
        r.raise_for_status()
        return r.text, pb

    def upload(self) -> dict[Path, str]:
        for f in self.file_list:
            self.check_split_video(f)
        print(self.file_list)
        self.executor = ThreadPoolExecutor(max_workers=4)
        d = {}
        future_to_path = {
            self.executor.submit(self.upload_file, path): path
            for path in self.file_list
        }
        for future in as_completed(future_to_path):
            p = future_to_path[future]
            try:
                html, pb = future.result()
                u = self.get_upload_url(html)
                d[p] = u
                pb.write(f"{p} uploaded to {u}")
            except Exception as ex:
                print(ex)
        return d

    def stop(self, interrupt=False):
        if interrupt:
            self.executor.shutdown(wait=False, cancel_futures=True)
        else:
            self.executor.shutdown()
        self.session.close()

    @staticmethod
    def get_upload_url(html: str) -> str:
        regex = (
            r'onclick=\"this.select\(\);" value="(https://www\d{1,3}'
            r".zippyshare.com/v/[a-zA-Z\d]{8}/file.html)"
        )
        url = re.search(regex, html)
        if not url:
            raise Exception("Failed to extract file URL.")
        return url.group(1)

    def check_split_video(self, path: Path):
        if path.stat().st_size > self.SIZE_LIMIT:
            if not self.split_videos:
                raise FileIsTooBigException(
                    f"{path.name} is bigger than 500MB. If you want this script to "
                    f"automatically zip it pass -s argument."
                )
            mt = self.get_mime_type(path)
            if mt is not None and "video" in mt:
                print(f"Splitting video: {path.name}")
                self.file_list.extend(self._split_video(path))
                self.file_list.remove(path)
            else:
                raise ZPUploadException(
                    f"Couldn't get mimetype for file: {path.name}. "
                    f"File size is too big for upload and can't split automatically. "
                    f"Please manually split it."
                )

    def _split_video(self, path: Path) -> List[Path]:
        new_videos = []
        duration = Duration()
        split_n = (path.stat().st_size // self.SIZE_LIMIT) + 1
        try:
            # fmt: off
            out = subprocess.run([
                "ffprobe", "-v", "quiet","-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path.resolve())
                ],
                check=True, capture_output=True)
            v_duration_in_seconds = float(out.stdout)
            # fmt: on
            new_duration_seconds = v_duration_in_seconds / split_n
            duration.add_seconds(new_duration_seconds)
        except subprocess.CalledProcessError as e:
            print(e)
            return new_videos
        # fmt: off
        command = [
            "ffmpeg","-i", str(path.resolve()), "-c", "copy", "-map", "0",
            "-f", "segment", "-segment_time", f"{duration.hours}:{duration.minutes}:{duration.seconds}",
            "-reset_timestamps", "1", "-v", "quiet",
            str(path.with_suffix(f".%02d{path.suffix}").resolve()),
        ]
        # fmt: on
        try:
            subprocess.run(command, check=True)
            if path.parent.is_dir():
                for f in path.parent.iterdir():
                    if (
                        f.is_file()
                        and re.match(
                            re.escape(path.stem) + r"\.\d{2}" + re.escape(path.suffix),
                            f.name,
                        )
                        is not None
                    ):
                        new_videos.append(f)
        except subprocess.CalledProcessError as e:
            print(e)
        return new_videos


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", "-f", nargs="*", type=Path, help="Files to upload")
    parser.add_argument(
        "--directory",
        "-d",
        nargs="*",
        type=Path,
        help="Directories containing files to upload",
    )
    parser.add_argument(
        "--split", "-s", action="store_true", help="Whether to zip files or not"
    )
    parser.add_argument("--output", "-o", nargs=1, type=Path, help="Output file")
    parser.add_argument(
        "--retries",
        "-r",
        type=int,
        choices=[0, 1, 2, 3, 4, 5],
        default=0,
        help="How many times to re-attempt failed uploads",
    )
    parser.add_argument("--proxy", help="HTTPS proxy. <IP>:<PORT>")
    args = parser.parse_args()

    if args.file:
        for p in args.file:
            if not p.exists():
                raise FileNotFoundError(f"{p.name} file does not exist")
            if not p.is_file():
                raise ZPUploadException(f"{p.name} is not a file")

    if args.directory:
        for d in args.directory:
            if not d.exists():
                raise FileNotFoundError(f"{d.name} directory does not exist")
            if not d.is_dir():
                raise NotADirectoryError(f"{d.name} is not a directory")

    return args


def get_file_list_from_dir(d: Path) -> List[Path]:
    file_list = []
    for path in d.iterdir():
        if path.is_file():
            print(f"{path} found...")
            file_list.append(path)
        elif path.is_dir():
            fl = get_file_list_from_dir(path)
            file_list.extend(fl)
        else:
            continue
    return file_list


if __name__ == "__main__":
    args = parse_args()
    files = []
    if args.file:
        files.extend(args.file)
    elif args.directory:
        for directory in args.directory:
            f_list = get_file_list_from_dir(directory)
            files.extend(f_list)

    uploader = ZPUploader(files, args.split, args.retries)
    try:
        dict_ = uploader.upload()
    except KeyboardInterrupt:
        print("\nSTOPPING ALL UPLOADS...\n")
        uploader.stop(interrupt=True)
    finally:
        uploader.stop()
