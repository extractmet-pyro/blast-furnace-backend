# Third-party notices - backend

The backend uses the following open-source packages (exact versions in `requirements.txt`).
Each is distributed under its own licence; the licence texts are included in the installed
packages.

| Package | Version | Licence |
|---|---|---|
| FastAPI | 0.162.0 | MIT |
| Starlette | 1.7.0 | BSD-3-Clause |
| Uvicorn (standard extras) | 0.54.0 | BSD-3-Clause |
| Pydantic | 2.13.5 | MIT |
| python-multipart | 0.0.32 | Apache-2.0 |
| SQLAlchemy | 2.1.8 | MIT |
| Alembic | 1.20.1 | MIT |
| psycopg2-binary | 2.9.13 | LGPL-3.0-or-later with OpenSSL exception (used unmodified, dynamically) |
| PyJWT | 2.15.0 | MIT |
| NumPy | 2.5.3 | BSD-3-Clause (bundled components: 0BSD, MIT, Zlib, CC0-1.0) |
| SciPy | 1.18.1 | BSD-3-Clause |
| pandas | 3.0.5 | BSD-3-Clause |
| scikit-learn | 1.9.1 | BSD-3-Clause |
| joblib | 1.6.0 | BSD-3-Clause |
| OSQP | 1.1.3 | Apache-2.0 |
| openpyxl | 3.1.5 | MIT |
| PyYAML | 6.0.3 | MIT |
| jsonschema | 4.26.0 | MIT |
| HTTPX | 0.28.1 | BSD-3-Clause |
| websockets | 17.4 | BSD-3-Clause |
| pytest (development only) | 9.1.1 | MIT |
| asyncua (optional, on-premises collector only, not installed by default) | - | LGPL-3.0 |

Thermochemical data are taken from published sources cited in `app/physics/species.py` and
`docs/MODEL_EQUATIONS.md` (NIST-JANAF, Kubaschewski et al., Robie and Hemingway, Sigworth and
Elliott); values are rounded and re-expressed, not copied tables.
