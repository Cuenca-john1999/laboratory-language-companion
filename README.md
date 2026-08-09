# LLC — Laboratory Language Companion

<p align="center">
  <strong>Local-first language learning for laboratory and life-science professionals.</strong>
</p>

<p align="center">
  Desktop · Local AI · Open Source · Science-focused
</p>

---

## 🧪 What is LLC?

**Laboratory Language Companion (LLC)** is an open-source desktop application for learning languages with a strong focus on **laboratory, biomedical and life-science environments**.

The project combines general language learning with professional and scientific communication, guided study, structured educational sources, exercises and a locally running AI tutor.

LLC started as **DeutschOS**, a personal German-learning platform. As the project grew, its scope became broader:

> not just learning German — but learning the language you need to study, communicate and work in science.

German remains the first and most developed language implementation while the project evolves toward a reusable multilingual architecture.

---

## 🎯 The idea

Most language-learning applications are designed around everyday situations.

That is useful — but someone preparing to work in a laboratory also needs to understand things like:

- laboratory instructions;
- scientific vocabulary;
- sample handling;
- safety procedures;
- documentation;
- communication with colleagues;
- quality-control terminology;
- scientific explanations;
- professional interviews;
- and eventually complex technical material.

LLC aims to connect both worlds.

You still learn the language itself — grammar, vocabulary, reading, writing, listening and communication — but the system increasingly understands **why you are learning it**.

For example:

```text
General German
↓
Professional German
↓
Laboratory communication
↓
Clinical / biomedical / scientific specialization
```

---

## 🌍 Language model

The long-term structure is intended to separate the language from the professional domain:

```text
Language
├── German
├── English
├── Spanish
└── ...

Professional domain
├── Clinical Laboratory
├── Biomedical Research
├── Microbiology
├── Molecular Biology
├── Biotechnology
├── Chemistry
├── Pharmaceutical Sciences
└── ...
```

A learner could therefore eventually follow paths such as:

```text
German
└── Clinical Laboratory
    └── A2 → B2
```

or:

```text
English
└── Biotechnology
    └── B1 → C1
```

German is currently the reference implementation used to validate the architecture.

---

## 🤖 Local-first AI

LLC is designed around **local AI inference**.

The AI tutor and supporting language-model functionality run on the user's own computer through a compatible local runtime.

The current implementation is developed around **LM Studio**.

This means the project does not require a hosted AI service as its core architecture.

Local models can be used for tasks such as:

- language explanations;
- tutoring;
- contextual exercises;
- guided practice;
- educational-library queries;
- source-aware answers;
- study assistance.

Model support and requirements may evolve as the project develops.

---

## 📚 Source-aware learning

LLC includes an educational-library system designed to work with locally stored learning material.

Instead of treating every document as an isolated PDF, the system can organize educational sources, document versions and structured information for use in study workflows.

Current development includes infrastructure for areas such as:

- document ingestion;
- document versioning;
- page-level comparison;
- OCR-related workflows;
- structured extraction;
- document review;
- processing runs;
- source coverage;
- document auditing;
- educational-library queries.

Private learning material remains outside the public repository.

---

## 🔬 Why science?

LLC is intentionally specialized.

The goal is not to compete with general-purpose language-learning applications by trying to cover every possible learner.

Instead, LLC focuses on people who study or work in areas such as:

- clinical laboratory science;
- biomedical sciences;
- microbiology;
- molecular biology;
- biotechnology;
- chemistry;
- pharmaceutical sciences;
- medical research;
- pathology;
- quality control;
- related life-science fields.

General language competence remains essential — the scientific specialization is built **on top of it**, not instead of it.

---

## 🖥️ Desktop-first

LLC is a desktop application.

Mobile support is **not currently a project goal**.

The application is being developed around workflows that benefit from a computer:

- local language models;
- large educational libraries;
- document analysis;
- structured study;
- scientific material;
- local databases;
- advanced tutoring workflows.

The current native desktop integration targets **macOS**.

Support for additional desktop platforms may be explored in the future.

---

## 🏗️ Current architecture

The existing project uses:

- **FastAPI** — application API and backend services
- **Next.js** — user interface
- **Swift** — native macOS controller / launcher
- **SQLite** — local application and educational-library data
- **LM Studio** — current local model runtime
- **Local embedding models** — semantic search and retrieval

The architecture is actively evolving as the original German-specific implementation is generalized into LLC.

---

## 🧭 Project status

> **Early development / pre-release**

LLC is not yet presented as a finished multilingual product.

The current application is a functioning German-first system that originated as **DeutschOS** and is now undergoing a larger transition into Laboratory Language Companion.

The initial public-development phase focuses on:

- safely publishing the existing codebase;
- preserving its Git history;
- separating private educational material from public code;
- migrating the DeutschOS product identity to LLC;
- defining language-independent architecture;
- preserving the existing German experience;
- preparing the system for future scientific language paths.

Progress is tracked through the repository's GitHub Issues.

---

## 🛣️ Roadmap

### Foundation

- [ ] Audit the complete repository before public code import
- [ ] Establish public/private data boundaries
- [ ] Import and preserve the existing DeutschOS Git history
- [ ] Transition product identity to LLC
- [ ] Publish development and contribution documentation

### Architecture

- [ ] Separate language-independent functionality from German-specific behavior
- [ ] Define a language-profile / language-pack architecture
- [ ] Define reusable scientific-domain profiles
- [ ] Preserve CEFR-based progression where appropriate
- [ ] Generalize tutor and educational-library context

### German reference implementation

- [ ] Preserve the current German learning path
- [ ] Expand laboratory-oriented German content
- [ ] Improve professional communication workflows
- [ ] Integrate scientific vocabulary and scenarios more deeply

### Future validation

- [ ] Introduce a second language implementation
- [ ] Validate that the architecture is genuinely language-independent
- [ ] Expand scientific-domain specialization

The roadmap will evolve with the project.

---

## 🔐 Privacy and repository boundaries

The application code can be open source while a user's educational library remains private.

The public repository must not contain:

- copyrighted books or PDFs without redistribution rights;
- private OCR corpora;
- personal educational databases;
- study histories;
- model weights;
- backups;
- credentials;
- API keys;
- private logs;
- machine-specific personal data.

Public development should use synthetic fixtures, examples and openly redistributable resources.

---

## 🧑‍💻 Open source

LLC is being developed as an open-source project.

There is no requirement for a commercial account or subscription as part of the project's intended core architecture.

The goal is simple:

> build a useful language-learning tool for people in science, and make the code available to anyone who wants to use, study or improve it.

---

## 🤝 Contributing

LLC is still undergoing its transition from DeutschOS, so contribution guidelines will evolve alongside the architecture.

Bug reports, technical discussions and well-scoped contributions are welcome.

Before contributing, please avoid including:

- copyrighted educational material;
- private databases;
- personal information;
- credentials;
- local model files;
- sensitive logs.

See `CONTRIBUTING.md` for more information once the public-development foundation is complete.

---

## 📜 License

The intended open-source license is:

**GNU General Public License v3.0 — GPL-3.0-only**

A formal `LICENSE` file will be included with the public source release.

---

## ❤️ Origin

LLC began as a personal project called **DeutschOS**, created to build a better way to learn German using structured educational material and local AI.

The project gradually grew into something broader:

**Laboratory Language Companion.**

A language-learning environment built around the needs of people who work, study and communicate in science.

---

<p align="center">
  <strong>LLC</strong><br>
  Laboratory Language Companion
</p>

<p align="center">
  <em>Learn the language. Work the science.</em>
</p>
