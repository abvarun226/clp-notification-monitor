import logging
from pathlib import Path
from typing import Generator, List

import grpc

from clp_notification_monitor.seaweedfs_monitor.grpc.filer_pb2 import (
    Entry,
    EventNotification,
    SubscribeMetadataRequest,
)
from clp_notification_monitor.seaweedfs_monitor.grpc.filer_pb2_grpc import (
    SeaweedFilerServicer,
    SeaweedFilerStub,
)
from clp_notification_monitor.seaweedfs_monitor.notification_message import (
    S3NotificationMessage,
    SeaweedFID,
)


class SeaweedFSClient(SeaweedFilerServicer):
    """
    This class represents SeaweedFS Filer gRPC client.

    It maintains the gRPC channel that communicates with the Filer server. It
    also provides necessary methods to send requests and receive responses from
    the server.
    """

    def __init__(self, client_name: str, endpoint: str, logger: logging.Logger):
        """
        Constructor.

        :param client_name: The name of the client. Used by the server for
            logging purpose.
        :param endpoint: The endpoint of the Filer server.
        :param logger: Global logging handler.
        """
        self._client_name: str = client_name
        self._channel: grpc.Channel = grpc.insecure_channel(endpoint)
        self._stub: SeaweedFilerStub = SeaweedFilerStub(self._channel)
        self._logger: logging.Logger = logger

    def close(self) -> None:
        """
        Closes the channel.
        """
        self._channel.close()

    def s3_file_ingestion_listener(
        self, since_ns: int = 0, store_fid: bool = True
    ) -> Generator[S3NotificationMessage, None, None]:
        """
        Creates a generator to receive Filer S3 file ingestion.

        In SeaweedFS, only ingested files under `/buckets/` are considered to be
        accessible through S3 APIs. This method will subscribe to Filer metadata
        changes. It filters the file creation events, and formats the response
        as an instance of S3NotificationMessage with all the data required from
        CLP database.
        :param since_ns: Starting timestamp to listen to notifications.
        :param store_fid: A boolean flag indicating whether the fid of chunks
        should be stored.
        :yield: A notification message.
        """
        request: SubscribeMetadataRequest = SubscribeMetadataRequest()
        request.client_name = self._client_name
        request.since_ns = since_ns
        request.path_prefixes.append("/buckets/")
        self._logger.info("Subscribe to Filer gRPC metadata changes.")
        for response in self._stub.SubscribeLocalMetadata(request):
            try:
                event: EventNotification = response.event_notification
                new_entry: Entry = event.new_entry
                if 0 == len(new_entry.name) or 0 != len(event.old_entry.name):
                    continue
                if new_entry.is_directory:
                    continue
                full_path: Path = Path(response.directory) / Path(new_entry.name)
                if 3 >= len(full_path.parts):
                    continue
                s3_bucket: str = full_path.parts[2]
                s3_key: str = str(Path(*full_path.parts[3:]))
                file_size: int = new_entry.attributes.file_size
                fid_list: List[SeaweedFID] = []
                if store_fid:
                    for chunk in new_entry.chunks:
                        fid_list.append(
                            SeaweedFID(chunk.fid.volume_id, chunk.fid.file_key, chunk.fid.cookie)
                        )
                yield S3NotificationMessage(s3_bucket, s3_key, file_size, fid_list)
            except Exception as e:
                self._logger.error(f"Exception on Filer gRPC response: {e}")
