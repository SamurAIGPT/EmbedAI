from boat_sdk import (
    ApiClient,
    Configuration,
    wait_until_ready,
)

from boat_sdk.api.boat_api import BoatApi
from boat_sdk.models.create_sandbox_request import CreateSandboxRequest
from boat_sdk.models.resume_request import ResumeRequest
from boat_sdk.models.stop_request import StopRequest
from boat_sdk.models.command_request import CommandRequest
from boat_sdk.models.file_write_request import FileWriteRequest
from boat_sdk.models.prompt_request import PromptRequest

from app.core.config import settings


class BoatNotConfigured(RuntimeError):
    """Raised when a Boat sandbox is needed but BOAT_API_KEY is not set."""


class ASCIIBoxService:
    """Service for interacting with the Boat sandbox API.
    This service provides methods to create, retrieve, stop, resume, and delete
    sandboxes using the Boat API. It handles the configuration and authentication
    required to communicate with the API.

    """

    def _configuration(self) -> Configuration:
        if not settings.BOAT_ENABLED:
            raise BoatNotConfigured(
                "Boat sandboxes are not configured (BOAT_API_KEY is empty)."
            )
        return Configuration(
            host=settings.BOAT_BASE_URL,
            access_token=settings.BOAT_API_KEY,
        )

    def create_box(self, ttl_seconds: int = 1800):
        configuration = self._configuration()

        with ApiClient(configuration) as client:
            sandbox_api = BoatApi(client)

            created = sandbox_api.create(
                create_sandbox_request=CreateSandboxRequest(
                    ttl_seconds=ttl_seconds,
                    no_env=True,
                    org=settings.BOAT_ORG_ID or None,
                )
            )

            sandbox_id = created.sandbox.id

            # Creation starts asynchronously.
            # Wait until machine is usable.
            return wait_until_ready(
                sandbox_api,
                sandbox_id,
            )

    def get_box(self, box_id: str):
        configuration = self._configuration()

        with ApiClient(configuration) as client:
            sandbox_api = BoatApi(client)

            return sandbox_api.get(box_id)

    def stop_box(self, box_id: str):
        configuration = self._configuration()

        with ApiClient(configuration) as client:
            sandbox_api = BoatApi(client)

            return sandbox_api.stop(box_id, StopRequest())

    def resume_box(self, box_id: str):
        configuration = self._configuration()

        with ApiClient(configuration) as client:
            sandbox_api = BoatApi(client)

            sandbox_api.resume(
                box_id,
                ResumeRequest(no_env=True),
            )

            return wait_until_ready(
                sandbox_api,
                box_id,
            )

    def delete_box(self, box_id: str):
        configuration = self._configuration()

        if not box_id:
            raise ValueError("box_id must be provided to delete a box.")

        with ApiClient(configuration) as client:
            sandbox_api = BoatApi(client)

            return sandbox_api.delete_sandbox(
                x_ascii_confirm_delete=box_id,
                sandbox_id=box_id,
            )

    def run_command(
        self,
        box_id: str,
        command: str,
        timeout_seconds: int = 30,
        detached: bool = False,
    ):
        configuration = self._configuration()

        with ApiClient(configuration) as client:
            sandbox_api = BoatApi(client)

            return sandbox_api.command(
                box_id,
                CommandRequest(
                    command=command,
                    cwd=".",
                    timeout_seconds=timeout_seconds,
                    detached=detached,
                ),
            )

    def command_status(self, box_id: str, process_id: int):
        configuration = self._configuration()

        with ApiClient(configuration) as client:
            sandbox_api = BoatApi(client)

            return sandbox_api.command_status(box_id, process_id)

    def write_file(
        self,
        box_id: str,
        path: str,
        content: str,
    ):
        configuration = self._configuration()

        with ApiClient(configuration) as client:
            sandbox_api = BoatApi(client)

            return sandbox_api.write_file(
                box_id,
                FileWriteRequest(
                    path=path,
                    content=content,
                ),
            )

    def read_file(self, box_id: str, path: str):
        configuration = self._configuration()

        with ApiClient(configuration) as client:
            sandbox_api = BoatApi(client)

            return sandbox_api.read_file(box_id, path)

    def prompt(self, box_id: str, provider: str, prompt: str):
        configuration = self._configuration()

        with ApiClient(configuration) as client:
            sandbox_api = BoatApi(client)

            return sandbox_api.prompt(
                box_id,
                PromptRequest(provider=provider, prompt=prompt),
            )

    def prompt_run_status(self, box_id: str, prompt_id: str):
        configuration = self._configuration()

        with ApiClient(configuration) as client:
            sandbox_api = BoatApi(client)

            return sandbox_api.prompt_run_status(box_id, prompt_id)


ascii_box_service = ASCIIBoxService()
