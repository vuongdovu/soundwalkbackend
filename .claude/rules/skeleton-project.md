# Skeleton Project Rules

This skeleton includes common infrastructure most apps need. These rules ensure taking it in YOUR direction is fast and painless.

---

## Single Source of Identity

- Project name defined in **ONE file only**
- All other references derive from that single source
- Renaming the project = changing one value

---

## Separation: Infrastructure vs Business

| Directory | Contains | Rule |
|-----------|----------|------|
| `/core/` or `/infrastructure/` | Skeleton code | Don't modify, just use |
| `/app/` or `/features/` | Your business code | Extend here |

Never mix infrastructure and business logic in the same file.

---

## Clear Extension Points

Mark where new code goes:

```
# Models: Add your domain models here
# Routes: Add your endpoints here
# Services: Add your business logic here
```

A developer should know WHERE to add code without reading the whole codebase.

---

## No Cruft to Delete

The skeleton contains:
- Working infrastructure
- Base classes to extend
- Configuration templates

The skeleton does NOT contain:
- Example features
- Demo models
- Placeholder routes
- Sample business logic

Nothing to delete. Just add your code.

---

## Decision Isolation

Each infrastructure concern is independently swappable:

| Concern | Can Change Without Affecting |
|---------|------------------------------|
| Auth strategy | Database, API routes |
| Database choice | Auth, caching |
| Cache layer | Database, auth |
| Queue system | All of the above |

Changing one decision should not cascade.

---

## Generic Infrastructure Naming

Infrastructure uses function-based names, not domain names:

| Good (Generic) | Bad (Domain-Specific) |
|----------------|----------------------|
| `BaseModel` | `User` |
| `BaseService` | `OrderService` |
| `BaseRepository` | `ProductRepository` |
| `authenticate()` | `loginUser()` |

Your domain names belong in YOUR code, not the skeleton.

---
