# DealWise

**AI-Powered Contract & Deal Intelligence Platform**

DealWise is a contract management and intelligence platform designed to help organisations manage agreements, track vendors, monitor contract lifecycles, and build a foundation for data-driven deal decisions.

## Project Status

**Current release: Phase 1 — Foundation**

The current implementation focuses on the core contract management experience. Further intelligence and analytics capabilities are planned for subsequent phases.

## Features

### Contract Repository
- Centralised contract repository.
- Search and filter contracts.
- Organise contracts by vendor, category, department and status.
- Track contract values, expiry dates and lifecycle information.
- Support for original agreements and lifecycle-related records.

### Vendor Management
- Centralised vendor records.
- Link multiple contracts to a single vendor.
- Search vendors.
- Manage vendor details, aliases, websites and notes.
- Identify potential duplicate vendors.
- Merge duplicate vendor records (administrator functionality).

### Dashboard
- Overview of agreements and active contracts.
- Upcoming contract deadlines.
- Contract value summaries by currency.
- Category-wise contract overview.
- Recently added contracts.

### Security & Access
- User authentication and role-based access.
- Contract visibility and access controls.
- Audit-conscious handling of sensitive information.

## Technology Stack

- **Backend:** Python
- **Application framework:** Gradio
- **Database:** SQLite / SQLAlchemy / SQLModel
- **Architecture:** Modular Python packages
- **Testing:** Pytest

## Project Structure

```text
DealWise/
├── app.py
├── flowsettings.py
├── libs/
│   ├── dealwise/
│   │   ├── dealwise/
│   │   │   ├── db/
│   │   │   ├── security/
│   │   │   ├── services/
│   │   │   └── ui/
│   │   └── tests/
│   ├── kotaemon/
│   └── ktem/
├── docs/
├── scripts/
├── templates/
├── pyproject.toml
└── uv.lock
```

## Current Scope and Roadmap

| Phase | Focus | Status |
|---|---|---|
| Phase 1 | Foundation, dashboard, contracts, vendors and lifecycle management | Implemented; undergoing refinement |
| Phase 2 | Contract intelligence, extraction, provenance and citation-backed analysis | Planned |
| Phase 3 | Deal evaluation and structured assessments | Planned |
| Phase 4 | Historical outcomes and performance analytics | Planned |
| Phase 5 | Decision support, insights and reporting | Planned |

## Development Setup

DealWise is developed using Python 3.11.

The application uses environment-based configuration. Before running locally, configure your environment and dependencies according to the project configuration.

For a local development run:

```bash
export DEALWISE_ENABLED=true
export KH_FEATURE_USER_MANAGEMENT_PASSWORD='your-strong-password'
python app.py
```

The application launches locally at:

```text
http://127.0.0.1:7860
```

Do not commit `.env` files, credentials, database files or local application data.

## Important Notes

- This repository currently represents the Phase 1 implementation.
- Contract intelligence, automated extraction, deal evaluation and advanced reporting are not yet implemented.
- Configure authentication credentials before exposing the application beyond your local machine.
- Do not use the inherited Chat or Files functionality for restricted contracts until DealWise-specific access controls are enforced across those workflows.

## Acknowledgements

DealWise builds upon and extends the Kotaemon and KTEM open-source framework components. Their applicable original licensing, copyright notices and attribution are retained in this repository.

The underlying framework is distributed under the Apache License 2.0.

## License

This project retains the applicable Apache License 2.0 terms and upstream notices. See [LICENSE.txt](LICENSE.txt) and [NOTICE](NOTICE).
