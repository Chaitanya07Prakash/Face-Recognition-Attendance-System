import multiprocessing
import signal
import time

from django.core.management.base import BaseCommand

from attendance.camera_worker import run_camera_worker
from attendance.models import Camera


class Command(BaseCommand):
    help = "Launches one capture-worker process per enabled Camera row (Phase 4: multi-camera fleet)"

    def handle(self, *args, **options):
        cameras = list(Camera.objects.filter(is_enabled=True))
        if not cameras:
            self.stdout.write(self.style.WARNING("No enabled cameras found. Add some in the admin first."))
            return

        stop_event = multiprocessing.Event()
        processes = []

        def handle_shutdown(signum, frame):
            self.stdout.write("Shutdown signal received, stopping camera workers...")
            stop_event.set()

        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)

        for camera in cameras:
            process = multiprocessing.Process(
                target=run_camera_worker,
                args=(str(camera.id), camera.rtsp_url, stop_event),
                name=f"camera-worker-{camera.name}",
                daemon=True,
            )
            process.start()
            processes.append(process)
            self.stdout.write(f"Started worker for '{camera.name}' (pid {process.pid})")

        self.stdout.write(self.style.SUCCESS(f"{len(processes)} camera worker(s) running. Ctrl+C to stop."))

        try:
            while not stop_event.is_set():
                time.sleep(1)
                # Restart any worker process that died unexpectedly (crashed,
                # not a clean shutdown) rather than silently losing that camera.
                for i, process in enumerate(processes):
                    if not process.is_alive() and not stop_event.is_set():
                        camera = cameras[i]
                        self.stdout.write(self.style.WARNING(f"Worker for '{camera.name}' died, restarting..."))
                        new_process = multiprocessing.Process(
                            target=run_camera_worker,
                            args=(str(camera.id), camera.rtsp_url, stop_event),
                            name=f"camera-worker-{camera.name}",
                            daemon=True,
                        )
                        new_process.start()
                        processes[i] = new_process
        except KeyboardInterrupt:
            stop_event.set()

        for process in processes:
            process.join(timeout=10)
        self.stdout.write("All camera workers stopped.")
