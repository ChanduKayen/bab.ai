# Bab.ai — The Intelligent OS for Construction

![version](https://img.shields.io/badge/version-1.0.0-blue)
![python](https://img.shields.io/badge/python-3.8%2B-green)
![OpenAI](https://img.shields.io/badge/OpenAI-74aa9c?logo=openai&logoColor=white)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Technologies](#technologies)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Usage](#usage)
- [Contributing](#contributing)
- [License](#license)

## Overview

**Bab.ai** is an intelligent operating system designed specifically for the construction industry.
It is a **WhatsApp-first platform** that seamlessly connects **Procurement**, **Site Management**, and **Credit** teams, enabling real-time collaboration and streamlined workflows across all stakeholders.

## Key Features

- 🟢 **WhatsApp Integration:** Communicate and manage operations directly via WhatsApp, making it accessible and intuitive for on-site teams.
- 🟣 **Unified Platform:** Bridges procurement, site management, and credit processes for holistic project oversight.
- 🟡 **AI-Powered Automation:** Leverages advanced AI to automate routine tasks, answer queries, and provide actionable insights.
- 🔵 **Real-Time Collaboration:** Ensures all parties are connected and informed, reducing delays and miscommunication.
- 🟠 **Secure Data Handling:** Utilizes robust backend technologies for secure and reliable data management.

## Technologies

| Technology               | Version  | Purpose                   |
| ------------------------ | -------- | ------------------------- |
| 🐍 Python                | ≥3.8     | Core programming language |
| ⚡ FastAPI               | ^0.68.0  | Backend API framework     |
| 🤖 OpenAI                | ^0.27.0  | NLP & AI capabilities     |
| 🔗 LangChain             | ^0.0.150 | AI workflow orchestration |
| 🗄️ Postgresql            | ≥13.0    | Database management       |
| 💬 WhatsApp Business API | v2.0     | Communication channel     |
| 🔔 Webhook               | v1       | Real-time event handling  |

## Getting Started

### Prerequisites

1. Python 3.8 or higher
2. Postgresql 13.0 or higher
3. Git
4. WhatsApp Business API credentials
5. OpenAI API key

### Configuration

1. Clone the repository (use SSH for secure access): `git clone git@github.com:your-org/bab.ai.git` `cd bab.ai`
2. Set up virtual environment: `python -m venv .venv`
3. Activation of virtual environment: `.venv\Scripts\activate`
4. Install dependencies: `pip install -r requirements.txt`
5. Edit .env with your configuration.
   - `DATABASE_URL`: Postgresql connection string
   - `WHATSAPP_API_TOKEN`: WhatsApp Business API token
   - `OPENAI_API_KEY`: OpenAI API key
6. Running the Application: `uvicorn app.main:app --reload`

## Usage

- Interact with the platform primarily through **WhatsApp**, leveraging automated workflows for procurement, site management, and credit operations.
- Use the API endpoints (documented in the codebase) for integration with other systems or custom workflows.

### WhatsApp Integration

1. Configure webhook URL in WhatsApp Business API
2. Set up message templates in WhatsApp Business Manager
3. Test connection using the provided test endpoints

## Contributing

- Create feature branches for new development (e.g., `wa_features`)
- Keep your branch updated with `main` by merging regularly
- Submit Pull Requests for code review before merging to `main`
- Follow code style and commit message conventions
- Resolve merge conflicts promptly

## License

MIT License - See [LICENSE](LICENSE) for details

---

_For more details on configuration, deployment, and advanced usage, refer to the project documentation and code comments within the repository._
