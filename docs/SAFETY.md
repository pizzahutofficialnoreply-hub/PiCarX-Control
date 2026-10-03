# Safety and failure response

## Implemented protections

- The service starts with the selected adapter in a stopped state and requests a stop during shutdown.
- Pydantic rejects unsupported directions, speed values over 60%, and steering magnitudes over 30 degrees.
- A backend watchdog stops motion when no refreshed drive command arrives for 500 ms.
- Explicit stop requests invalidate older queued movement commands, then set motion and command values to stopped/zero.
- Adapter errors set a fault, attempt a stop, and return an HTTP error.
- The default mock adapter does not access motors or GPIO. Hardware mode uses only method signatures confirmed for the installed SunFounder library; incompatible or unavailable drivers are reported as disconnected and movement is refused.

The browser refreshes held drive commands every 180 ms. Releasing a button, a WebSocket disconnect, or a missed refresh leads to a stop. Browser page-exit stop is best effort; the server watchdog is the backup.

## Limits before real vehicle use

A software stop cannot guarantee that a failed driver or power stage stops the physical vehicle. Perform first tests with wheels raised, low speed, and a reachable physical power cutoff. This project has no physical emergency-stop circuit or obstacle detector.

The API currently has no authentication and permissive CORS. Keep it on a trusted local network; do not expose the API port to the internet. Add authentication and explicit control ownership before use on an untrusted network or with multiple operators.

Calibration is saved to `backend/data/settings.json`, but is not applied to motor commands until the installed driver API has been verified. Camera, sensors, and logs currently show an empty-state page; no fabricated data is shown.
