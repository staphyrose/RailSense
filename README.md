# RailSense-Private
# 🚆 RailSense – AI-Based Railway Track Health Monitoring System

**RailSense: Drive-by Monitoring System** is an AI-powered railway track health monitoring solution designed to identify potential railway track abnormalities through audio signal processing and machine learning.

The system analyzes railway audio recordings, extracts relevant signal features, and uses trained machine learning models to classify track conditions. The results are presented through an interactive dashboard that helps visualize train monitoring, detected abnormalities, and event information.

## 🎯 Project Overview

RailSense aims to improve railway track monitoring by integrating digital signal processing, artificial intelligence, and data visualization.

The system focuses on identifying three railway track conditions:

- **Normal** – Normal railway track sound patterns.
- **Superelivation** – Abnormal track condition.
- **Wheelburnt** – Wheel-related abnormality.

The project combines audio preprocessing, feature extraction, machine learning classification, and an interactive web-based monitoring dashboard.

## ✨ Key Features

- **Audio Signal Processing:** Processes railway audio recordings using digital signal processing techniques.
- **Noise Filtering:** Applies a Butterworth bandpass filter to reduce unwanted noise.
- **Feature Extraction:** Uses MFCC and Log-Mel Spectrogram representations for audio analysis.
- **AI-Based Classification:** Uses trained machine learning models to identify railway track conditions.
- **Interactive Dashboard:** Provides a centralized interface for monitoring train activities and detected events.
- **Train Monitoring Simulation:** Simulates multiple train movements across railway routes.
- **Event Detection:** Displays detected abnormalities with their classification and severity.
- **Event Visualization:** Provides signal-processing visualizations for analyzed audio recordings.

## 🖥️ Project Implementation & Screenshots

The actual project implementation, including screenshots of the developed dashboard, interfaces, and system visualizations, is available in the following folder:

📁 **`rail_sense_implementation_images/`**

This folder contains images captured from the actual RailSense software implementation and demonstrates the different modules and functionalities developed as part of the project.

## ⚙️ System Architecture

The RailSense system follows the workflow below:

1. Railway audio recordings are provided to the system.
2. Audio preprocessing is performed using a Butterworth bandpass filter.
3. Relevant audio features are extracted.
4. The trained machine learning model analyzes the extracted features.
5. The system classifies the audio into the corresponding condition.
6. The dashboard displays the classification results and associated event information.

## 🧠 Signal Processing

The audio processing pipeline includes:

- Audio loading using Librosa.
- Bandpass filtering using a Butterworth filter (50–3500 Hz).
- MFCC feature extraction.
- Log-Mel Spectrogram generation for signal visualization.

## 🤖 Machine Learning

Multiple machine learning approaches are explored and evaluated for railway audio classification.

The classification system identifies:

| Class | Description |
|---|---|
| Normal | Normal track condition |
| Superelivation | Abnormal track condition |
| Wheelburnt | Wheel-related abnormality |

## 🛠️ Technology Stack

| Category | Technologies |
|---|---|
| Programming Language | Python |
| Backend | Flask |
| Machine Learning | Scikit-learn |
| Audio Processing | Librosa, SciPy |
| Data Processing | NumPy, Pandas |
| Visualization | Matplotlib |
| Frontend | HTML, CSS, JavaScript |
| Database / Storage | Project-dependent |

## 🚉 Monitoring Environment

The current software implementation uses pre-recorded railway audio samples to simulate continuous monitoring of multiple trains.

Train movements and railway locations are represented through a simulated monitoring environment. The live hardware integration and physical deployment are considered part of the future implementation scope.

## 🚀 Future Scope

- Integration with real-time railway audio acquisition hardware.
- Deployment on railway inspection systems.
- Expansion of the railway audio dataset.
- Improved abnormality detection and classification.
- Integration with railway monitoring infrastructure.

## 👩‍💻 Project Information

**Project Name:** RailSense – Drive-by Monitoring System

**Domain:** Artificial Intelligence | Machine Learning | Digital Signal Processing | Railway Monitoring

**Project Type:** Final Year Engineering Major Project
