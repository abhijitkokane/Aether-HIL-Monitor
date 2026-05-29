# Aether-HIL: Real-Time Hardware-in-the-Loop Diagnostic Matrix & Control Plaza

A highly concurrent, event-driven Full-Stack Hardware-in-the-Loop (HIL) prototype dashboard designed to stream, monitor, and algorithmically analyze real-time video frames from connected Android device clusters at a crisp ~30Hz pacing. 

This project is a functional **Capability Showcase / Concept Demonstration** built to highlight advanced software architecture, computer vision metrics manipulation, and low-level system automation pipelines. It is explicitly not intended for hardened corporate production.

---

## 🛠️ System Architecture & Engineering Highlights

* **Asynchronous Concurrency Pipeline:** Built using `gevent` monkey-patching to unlock non-blocking POSIX network/process abstraction layers. It orchestrates background micro-workers that stream and process device inputs simultaneously without stalling the main execution engine.
* **Dynamic Hardware Hot-Plugging:** Includes an automated hardware lab orchestrator that actively polls the system's USB infrastructure, instantly spinning up dedicated analysis threads when new devices arrive, and cleanly pruning memory stores upon physical disconnection.
* **Computer Vision Diagnostic Engine:** Utilizes `OpenCV` to parse raw Android frame buffers. It executes algorithmic global frame-differencing (to catch complete OS UI stalls), isolated regional freeze tracking, and color-channel matrix checks to instantly flag low-level video decoder crashes (e.g., Green Screens or Graphic Dropouts).
* **Real-Time Event-Driven Streaming:** Leverages `Flask-SocketIO` to stream base64-encoded JPEG payloads efficiently to a responsive, multi-device Tailwind CSS triage board.

---

## 🚀 Getting Started

### Prerequisites
1. **Python 3.8+** installed on your host machine.
2. **Android SDK Platform Tools (ADB)** installed and globally accessible in your system's PATH.
3. One or more Android physical devices or emulators attached via USB with **USB Debugging enabled**.

### Installation & Launch
1. Clone this repository to your local environment:
   ```bash
   git clone [https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git](https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git)
   cd YOUR_REPO_NAME
