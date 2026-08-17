# Marine Fish Detection and Classification GUI

A desktop application for automated marine fish detection, classification, tracking, and analysis using object detection models.

The application currently supports YOLO-based models, with plans to support additional models such as RF-DETR.

The application provides a graphical interface for automated underwater video processing using AI-driven fish detection, classification, tracking, and annotation. It also allows users to review detected species, inspect Max-N abundance estimates, and export detection results for further analysis.

## Features

- Automated marine fish detection and classification
- Object detection and tracking
- Annotated video output
- Species-level detection summaries
- Max-N abundance estimates
- Low-confidence detection review
- Detection summary charts
- Project-based organisation of detection runs
- Support for additional compatible YOLO model weights
- Extensible architecture for future object detection models
- CPU processing and GPU acceleration on compatible hardware

---

## Requirements

The application is currently intended for **Windows 10 and Windows 11**.

Furthermore, you will need to have installed:

- Git
- Git LFS
- Pixi

Python does **not** need to be installed separately. Pixi automatically creates and manages the required Python environment and software dependencies.

> **Note:** The first installation may take several minutes. The application uses computer vision and machine-learning packages such as PyTorch, Ultralytics, OpenCV, and PyQt6. Allow approximately 5 GB of free disk space for the application environment and model weights, plus additional space for input videos and detection outputs.
---

## Installation

The complete installation can be performed through **Windows PowerShell**.

### 1. Open PowerShell

Open the Windows Start menu, search for **PowerShell**, and open **Windows PowerShell** or **Terminal**.

### 2. Install Git

Run:

```powershell
winget install --id Git.Git -e --source winget
```

### 3. Install Pixi

Run:

```powershell
powershell -ExecutionPolicy ByPass -c "irm -useb https://pixi.sh/install.ps1 | iex"
```

After Git and Pixi have finished installing, **close and reopen PowerShell**.

Confirm that Git and Pixi are available:

```powershell
git --version
pixi --version
```

Git for Windows includes Git LFS. Confirm that it is available:

```powershell
git lfs version
```

Then initialise Git LFS:

```powershell
git lfs install
```

### 4. Download the Application

Navigate to the folder where you would like to install the application.

For example, to install it on your Desktop:

```powershell
cd "$HOME\Desktop"
```

Clone the repository:

```powershell
git clone https://github.com/Brad420169/Marine-Fish-Detection-and-Classification-GUI.git
```

Enter the application folder:

```powershell
cd Marine-Fish-Detection-and-Classification-GUI
```

The repository uses Git LFS for large model-weight files. If required, ensure the model files have been downloaded with:

```powershell
git lfs pull
```

### 5. Install the Application Environment

Run:

```powershell
pixi install
```

Pixi will automatically download and configure the required Python environment and application dependencies.

The first installation may take several minutes depending on your computer and internet connection.

---

## Launching the Application

After installation, the application can be launched from PowerShell with:

```powershell
pixi run GUI
```

Alternatively, double-click:

```text
launch_gui.bat
```

from the application folder.

After the initial installation, you normally only need to use `launch_gui.bat` to start the application.

### Optional: Create a Desktop Shortcut

To make the application easier to access:

1. Find `launch_gui.bat` in the application folder.
2. Right-click the file.
3. Select **Show more options** if required.
4. Select **Send to → Desktop (create shortcut)**.
5. Rename the shortcut to **Marine Fish Detector** if desired.

The application can then be launched directly from the desktop without opening PowerShell.

---

## Using the Application

### Create a Project

When the application starts:

1. Enter a project name.
2. Select an output folder.
3. Click **Create**.

The selected output folder determines where detection results produced by the project are stored.

Project information is created locally and is not stored in the GitHub repository.

### Select a Video

Select a video using the **Video Input** section, or drag and drop a video directly into the application.

Supported video formats include:

- MP4
- AVI
- MOV
- MKV

### Select a Model

Choose the desired model from the **Model Input** section.

The repository includes marine fish detection model weights by default.

Additional compatible YOLO `.pt` model weights can be added using **Add model weights** within the application.

### Detection Settings

Three detection parameters can be configured before starting a run:

- **Detection confidence** — controls the minimum confidence required for a detection to be accepted.
- **Overlap sensitivity** — controls how overlapping detection boxes are handled.
- **Flag for review below** — determines the confidence threshold below which accepted detections are included in the review output.

The values can be adjusted using either the sliders or the editable numeric fields.

Hover over a setting name within the application for additional information about that parameter.

The default settings provide a reasonable starting point for detection.

### Run Detection

Click:

**▶ Run Detection**

The application displays:

- Detection progress
- Frames processed
- Processing speed
- Elapsed processing time
- Estimated remaining time
- Processing device
- Processing status

When processing finishes, the **Results** page opens automatically.

---

## Detection Results

Each completed detection run can produce the following outputs.

### Annotated Video

An annotated copy of the input video containing detected fish, bounding boxes, species classifications, confidence scores, frame numbers, and timestamps.

### Summary CSV

```text
track_summary.csv
```

Contains species-level detection information including:

- Species
- Max-N
- Max-N timestamp
- Max-N frame
- First observation
- Last observation
- Visible duration
- Observation span
- Unique tracks
- Total detections
- Mean detection confidence

### Review CSV

```text
low_confidence_review.csv
```

Contains accepted detections below the configured review-confidence threshold so that uncertain detections can be identified for manual review.

Information includes:

- Frame number
- Timestamp
- Species
- Confidence
- Track ID
- Bounding-box coordinates
- Notes field

### Max-N Example Frames

The application saves example frames associated with reported Max-N observations.

Target detections are highlighted to make it easier to visually inspect the fish contributing to the reported Max-N value.

### Detection Summary Charts

The Results page provides graphical summaries including:

- Peak abundance (Max-N)
- Total detections by species
- Visible duration by species
- Mean detection confidence

### Past Runs

Completed detection runs are stored within the project and can be reopened from the **Past Runs** section.

This allows previous results to be viewed without rerunning the detection pipeline.

---

## Updating the Application

To update to the latest version, open PowerShell in the application repository and run:

```powershell
git pull
```

Ensure any updated Git LFS files are downloaded:

```powershell
git lfs pull
```

Then update the Pixi environment:

```powershell
pixi install
```

Launch the application normally:

```powershell
pixi run GUI
```

or double-click:

```text
launch_gui.bat
```

---

## Project Structure

```text
Marine-Fish-Detection-and-Classification-GUI/
│
├── assets/             Application icons and graphics
├── models/             Marine fish detection model weights
├── scripts/            Application source code
│
├── launch_gui.bat      Windows application launcher
├── pixi.toml           Pixi environment configuration
├── pixi.lock           Locked dependency environment
├── .gitattributes      Git LFS configuration
├── .gitignore          Files excluded from version control
└── README.md
```

The following are generated locally and are not stored in the repository:

```text
.pixi/
projects/
__pycache__/
```

The `.pixi` directory contains the locally generated Pixi environment.

The `projects` directory contains locally generated application project information.

Detection outputs are stored in the output locations selected by the user when projects are created.

---

## Troubleshooting

### `git` is not recognised

Close PowerShell and open a new PowerShell window after installing Git.

Then run:

```powershell
git --version
```

If the command is still unavailable, restart Windows and try again.

### `pixi` is not recognised

Close PowerShell and open a new PowerShell window after installing Pixi.

Then run:

```powershell
pixi --version
```

### Git LFS model files did not download

From inside the application repository, run:

```powershell
git lfs install
git lfs pull
```

### The application does not launch

Open PowerShell in the application folder and run:

```powershell
pixi install
pixi run GUI
```

Launching through PowerShell will display any error messages that may help identify the problem.

### Updating causes dependency problems

Run:

```powershell
pixi install
```

The repository includes `pixi.lock`, which is used by Pixi to reproduce the application's dependency environment.

---

## Development

The application is written primarily in Python and uses technologies including:

- PyQt6
- Ultralytics YOLO
- PyTorch
- OpenCV
- Matplotlib
- NumPy

The development and runtime environment is managed using Pixi.

The application can be launched from the development environment using:

```powershell
pixi run GUI
```

### Planned Development

Potential future development includes:

- Support for additional object detection architectures such as RF-DETR
- Additional model-management functionality
- Further detection review and correction tools
- Expanded result visualisation and analysis

---

## Model and Dataset Attribution

The marine fish detection model weights included with this project were trained using the **Kona, Hawaii Dataset**, published by ReefOSHawaii on Roboflow Universe.

**Dataset citation:**

> ReefOSHawaii. (2022). *Kona, Hawaii Dataset*. Roboflow Universe.

The dataset is available from:

[Kona, Hawaii Dataset — Roboflow Universe](https://universe.roboflow.com/reefoshawaii/kona-hawaii)

The dataset is licensed under the **Creative Commons Attribution 4.0 International (CC BY 4.0) License**:

[Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/)

The model weights included in this repository were trained by the author of this project using the Kona, Hawaii Dataset. They are not original model weights supplied by ReefOSHawaii or Roboflow.

---

## License

The application source code is licensed under the **MIT License**. See `LICENSE` for details.

The included model weights were trained using the Kona, Hawaii Dataset described above. The underlying training dataset is provided by ReefOSHawaii under the **Creative Commons Attribution 4.0 International (CC BY 4.0) License**.
