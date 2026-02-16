import socket
import subprocess
import tempfile
from pathlib import Path

from .base import CRSUtils, DataType
from .common import rsync_copy, get_env
from .submit import SubmitHelper

OSS_CRS_BUILD_OUT_DIR = Path(get_env("OSS_CRS_BUILD_OUT_DIR"))


class LocalCRSUtils(CRSUtils):
    def __init__(self):
        super().__init__()

    def __init_submit_helper(self, data_type: DataType) -> SubmitHelper:
        OSS_CRS_SUBMIT_DIR = Path(get_env("OSS_CRS_SUBMIT_DIR"))
        shared_fs_dir = OSS_CRS_SUBMIT_DIR / data_type.value
        shared_fs_dir.mkdir(parents=True, exist_ok=True)
        return SubmitHelper(data_type, shared_fs_dir)

    def download_build_output(self, src_path: str, dst_path: Path) -> None:
        src = OSS_CRS_BUILD_OUT_DIR / src_path
        dst = Path(dst_path)
        rsync_copy(src, dst)

    def submit_build_output(self, src_path: str, dst_path: Path) -> None:
        src = Path(src_path)
        dst = OSS_CRS_BUILD_OUT_DIR / dst_path
        rsync_copy(src, dst)

    def skip_build_output(self, dst_path: str) -> None:
        with tempfile.NamedTemporaryFile() as tmp_file:
            tmp_file.write(b"skip")
            tmp_file.flush()
            dst = Path(dst_path)
            skip_file_path = dst.parent / f".{dst.name}.skip"
            self.submit_build_output(tmp_file.name, skip_file_path)

    def register_submit_dir(self, data_type: DataType, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        helper = self.__init_submit_helper(data_type)
        helper.register_dir(path, batch_time=10, batch_size=100)

    def register_shared_dir(self, local_path: Path, shared_path: str) -> None:
        if local_path.exists():
            raise FileExistsError(f"Local path '{local_path}' already exists")

        OSS_CRS_SHARED_DIR = Path(get_env("OSS_CRS_SHARED_DIR"))
        shared_path = OSS_CRS_SHARED_DIR / shared_path
        shared_path.mkdir(parents=True, exist_ok=True)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.symlink_to(shared_path)

    def register_fetch_dir(self, type: DataType, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        raise NotImplementedError("TODO: register_fetch_dir is not yet implemented")

    def submit(self, data_type: DataType, src: Path) -> None:
        helper = self.__init_submit_helper(data_type)
        helper.submit_file(src)

    def fetch(self, type: DataType, dst: Path) -> list[str]:
        raise NotImplementedError("TODO: fetch is not yet implemented")

    def get_service_domain(self, service_name: str) -> str:
        CRS_NAME = get_env("OSS_CRS_NAME")
        ret = f"{service_name}.{CRS_NAME}"

        # Check if the domain is accessible via DNS resolution
        try:
            socket.gethostbyname(ret)
        except socket.gaierror as e:
            raise RuntimeError(f"Service domain '{ret}' is not accessible: {e}")

        return ret

    def apply_patch_build(self, patch_path: Path, response_dir: Path) -> int:
        snapshot_image = get_env("OSS_CRS_SNAPSHOT_IMAGE")
        if not snapshot_image:
            raise RuntimeError("OSS_CRS_SNAPSHOT_IMAGE is not set")

        # Verify snapshot image exists
        result = subprocess.run(
            ["docker", "image", "inspect", snapshot_image],
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Snapshot image '{snapshot_image}' not found on Docker daemon"
            )

        # Set up request directory with the patch
        request_dir = response_dir / "_request"
        request_dir.mkdir(parents=True, exist_ok=True)
        response_dir.mkdir(parents=True, exist_ok=True)
        rsync_copy(patch_path, request_dir / "patch.diff")

        cmd = [
            "docker", "run", "--rm",
            "-v", f"{request_dir}:/request:ro",
            "-v", f"{response_dir}:/response:rw",
            "--privileged",
            "--shm-size=2g",
            snapshot_image,
            "/usr/local/bin/oss_crs_handler.sh", "/request", "/response",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        build_exit_file = response_dir / "build_exit_code"
        if build_exit_file.exists():
            return int(build_exit_file.read_text().strip())

        return result.returncode
