# Julius Call-Pilot

Call asistant for relationship managers that runs on their laptops during a call with a client, listens and analyzes conversation in real-time, and provides instant hints to the RM.

See `vision.md` for a broader vision of the system.

The `transcribe-local` folder contains our transcription script running locally.

The `transcribe-api` folder contains a version of the transcription script using Deepgram API.

The `steerandreport` is a demo of the system using a simulated call transcript but running a local LLM to produce hints. You need to run Ollama locally yourself.

The `steerandreport-live` is the live demo actually capturing your audio streams.

