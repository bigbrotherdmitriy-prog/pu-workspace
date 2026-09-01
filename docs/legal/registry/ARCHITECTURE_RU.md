# Архитектурная схема

```mermaid
flowchart TB
    U[Пользователь / браузер] --> API[PU Workspace API]
    API --> CORE[Project + Document + Contract + Task/Action Core]
    CORE --> GOV[Permissions + Approval + Audit]
    CORE --> DB[(PostgreSQL 16)]
    API --> Q[(BackgroundJob в PostgreSQL)]
    W1[Worker 1] --> Q
    W2[Worker 2] --> Q
    S[Scheduler] --> Q
    W1 --> CORE
    W2 --> CORE
    CORE --> IA[IntegrationAdapter]
    IA --> SA[StorageAdapter]
    IA --> CA[ChannelAdapter]
    IA --> AA[AIProviderAdapter]
    SA --> G[Текущий адаптер Google Drive]
    CA --> GM[Gmail / Telegram adapters]
    AA --> EXT[Разрешённый внешний AI]
    AA --> LOC[Будущий локальный/корпоративный endpoint]
    OCR[Локальный Tesseract/Poppler] --> CORE
```

Границы доверия:

1. Core не должен зависеть от идентификаторов конкретного провайдера.
2. Секреты задаются через окружение и не входят в поставку или публичное досье.
3. Оригиналы документов не изменяются при анализе; стандартизация выполняется в безопасной копии.
4. Внешний AI вызывается только через `AIProviderAdapter` и проектную политику.
5. Возможность полностью отключить внешний AI должна быть подтверждена приёмочным тестом выбранного релиза.

Исходная техническая спецификация: [`../../architecture-v5.2-provider-agnostic.md`](../../architecture-v5.2-provider-agnostic.md).
