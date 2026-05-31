# Alembic Migration System - Technical Specifications

**Document Type**: Architecture & Implementation Specification  
**Version**: 1.0  
**Date**: May 31, 2026  
**Audience**: Senior Backend Engineers, DevOps, Architects  

---

## 1. Architecture Overview

### System Design

```
┌──────────────────────────────────────────────────────────────┐
│                    Fover Backend Database                    │
│                   Migration Architecture                      │
└──────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ alembic/env.py - Migration Engine                           │
│ ├── Loads DATABASE_URL from environment                     │
│ ├── Imports all SQLAlchemy models                           │
│ ├── Compares current DB schema vs. model definitions        │
│ ├── Generates SQL (offline mode) or applies directly        │
│ └── Logs all migrations for auditing                        │
└─────────────────────────────────────────────────────────────┘
         ↑                           ↓
┌─────────────────┐         ┌──────────────────┐
│ SQLAlchemy ORM  │         │ PostgreSQL 12+   │
│  (11 Models)    │         │  (Production DB) │
└─────────────────┘         └──────────────────┘
         ↑                           ↓
┌─────────────────┐         ┌──────────────────┐
│ app/models/     │         │ alembic_version  │
│ ├── user.py     │         │ table (tracking) │
│ ├── match.py    │         └──────────────────┘
│ ├── league.py   │
│ └── ... (8 more)│
└─────────────────┘
```

---

## 2. Component Specifications

### 2.1 alembic/env.py (Primary Configuration)

**Purpose**: Bridge between SQLAlchemy models and database schema

**Key Implementations**:

#### Database URL Loading
```python
# Priority 1: Environment variable
database_url = os.getenv("DATABASE_URL")

# Priority 2: alembic.ini fallback
database_url = config.get_main_option("sqlalchemy.url")

# Auto-converts postgres:// to postgresql://
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
```

#### Metadata Configuration
```python
# Imports all models via app.models.__init__.py
from app.models import (User, Match, League, Team, Standings, 
                        Odds, Ad, News, MatchEvent, MatchH2H, 
                        MatchLineup)

# Uses SQLAlchemy's declarative base
target_metadata = Base.metadata
```

#### Comparison Options (Production-Grade)
```python
context.configure(
    # Detect column type changes (String(255) → String(500))
    compare_type=True,
    
    # Detect server defaults (func.now() changes)
    compare_server_default=True,
    
    # Better DDL generation for complex alterations
    render_as_batch=True,
    
    # Database connection or URL
    connection=connection  # or url=url
)
```

#### Offline Mode (SQL Generation)
- **Use Case**: Generate SQL without applying, for review
- **Command**: `alembic upgrade head --sql`
- **Output**: SQL script that can be reviewed/stored

#### Online Mode (Direct Execution)
- **Use Case**: Apply migrations directly to database
- **Command**: `alembic upgrade head`
- **Connection**: Direct SQLAlchemy connection pool

---

### 2.2 alembic.ini (Database Configuration)

**Key Sections**:

```ini
[alembic]
script_location = alembic
sqlalchemy.url = postgresql://fover_user:242374@localhost:5432/fover_db
file_template = %%(rev)s_%%(slug)s
timezone = utc
sqlalchemy.track_on = false
```

**Variables**:
- `script_location` - Points to alembic directory (contains env.py)
- `sqlalchemy.url` - Fallback database URL (overridden by DATABASE_URL env var)
- `file_template` - Naming pattern for migration files
- `timezone` - UTC for consistency across timezones
- `sqlalchemy.track_on` - false = clean migration history

---

### 2.3 Migration File Template (script.py.mako)

**Current Template**:
```python
"""${message}
Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from alembic import op
import sqlalchemy as sa

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}

def upgrade():
    pass

def downgrade():
    pass
```

**Usage**: Alembic uses this template when creating new migration files

---

### 2.4 Migration Versions Directory

**Structure**:
```
alembic/versions/
├── 0001_initial.py                          # Initial schema
├── 0002_add_users.py                        # Add users table
├── 0e5f3a506e20_add_team_ids_to_matches.py  # Add foreign keys
├── 61d3320badab_create_match_events_table.py # Match events
└── ... (more migrations)
```

**Naming Convention**:
- Format: `{revision_id}_{migration_name}.py`
- Revision ID: SHA1 hash (e.g., 0abc1234567f) or numeric (e.g., 0001)
- Migration name: Slug of your message (e.g., add_users_table)

**Migration File Structure**:
```python
"""Detailed description of changes"""

revision = 'abc1234567f'          # Unique identifier
down_revision = 'def9876543e'     # Parent migration
branch_labels = None              # For branched migrations
depends_on = None                 # Optional dependencies

def upgrade():
    """Apply this migration"""
    # SQL operations: add_column, create_table, etc.
    
def downgrade():
    """Reverse this migration"""
    # Exact reversal: drop_column, drop_table, etc.
```

---

## 3. Model Integration Specification

### 3.1 Model Requirements

Every model MUST:

1. **Inherit from Base**
```python
from app.db import Base

class MyModel(Base):
    # ...
```

2. **Define __tablename__**
```python
class MyModel(Base):
    __tablename__ = "my_models"  # Required
```

3. **Have primary key**
```python
id = Column(Integer, primary_key=True, index=True)
```

4. **Be imported in app/models/__init__.py**
```python
from app.models.my_model import MyModel
```

5. **Be imported in alembic/env.py**
```python
from app.models import MyModel
```

### 3.2 Current Models (All Integrated)

| Model | Table | Purpose | Status |
|-------|-------|---------|--------|
| User | users | Auth users | ✅ Integrated |
| Match | matches | Football matches | ✅ Integrated |
| League | leagues | Leagues | ✅ Integrated |
| Team | teams | Teams | ✅ Integrated |
| Standings | standings | League standings | ✅ Integrated |
| Odds | odds | Betting odds | ✅ Integrated |
| Ad | ads | Advertisements | ✅ Integrated |
| News | news | News articles | ✅ Integrated |
| MatchEvent | match_events | Match events | ✅ Integrated |
| MatchH2H | match_h2h | Head-to-head | ✅ Integrated |
| MatchLineup | match_lineups | Match lineups | ✅ Integrated |

---

## 4. Database URL Specifications

### 4.1 Format

```
postgresql://username:password@host:port/database

Example:
postgresql://fover_user:242374@localhost:5432/fover_db
```

### 4.2 Environment Variable Support

**Supported Formats**:
```bash
# Standard PostgreSQL URL
export DATABASE_URL="postgresql://user:pass@host:5432/db"

# Heroku format (auto-converted)
export DATABASE_URL="postgres://user:pass@host:5432/db"

# With special characters (URL encoded)
export DATABASE_URL="postgresql://user:p%40ssw0rd@host:5432/db"
```

**Conversion Logic** (in env.py):
```python
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
```

### 4.3 Connection Pooling

**Current Configuration**:
```python
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,      # Verify connections are alive
    pool_size=10,             # Base pool size
    max_overflow=20,          # Additional overflow connections
    future=True,              # SQLAlchemy 2.0 style
)
```

---

## 5. Migration Generation Workflow

### 5.1 Autogeneration Process

```
1. Load DATABASE_URL
   ↓
2. Create SQLAlchemy engine
   ↓
3. Connect to actual database
   ↓
4. Inspect current schema (tables, columns, constraints)
   ↓
5. Import all SQLAlchemy models
   ↓
6. Inspect model metadata
   ↓
7. COMPARE:
   └─ What's in DB vs. what's in models
   ↓
8. Detect differences:
   ├─ New tables?
   ├─ New columns?
   ├─ Type changes?
   ├─ Default changes?
   ├─ Index changes?
   └─ Constraint changes?
   ↓
9. Generate migration script
   ├─ upgrade() function
   └─ downgrade() function
   ↓
10. Save to alembic/versions/{id}_{name}.py
```

### 5.2 Comparison Features

| Feature | Enabled | Detects |
|---------|---------|---------|
| Table Creation | ✅ Yes | New tables |
| Column Addition | ✅ Yes | New columns |
| Column Removal | ✅ Yes | Dropped columns |
| Column Type Changes | ✅ Yes* | String(255) → String(500) |
| Column Defaults | ✅ Yes** | func.now() changes |
| Column Nullability | ✅ Yes | nullable changes |
| Index Creation | ✅ Yes | New indexes |
| Constraint Creation | ✅ Yes | New constraints |
| Foreign Keys | ✅ Yes | New FK relationships |

*Requires `compare_type=True`  
**Requires `compare_server_default=True`

---

## 6. Safety Specifications

### 6.1 Destructive Detection

Alembic will warn about potentially destructive operations:

```python
# Dropping a table with data
op.drop_table('users')  # WARNING: Data loss!

# Dropping a column with data
op.drop_column('users', 'email')  # WARNING: Data loss!

# Changing column type (may lose data)
op.alter_column('users', 'age', type_=sa.String())  # WARNING!
```

### 6.2 Reversibility Specification

All migrations must be reversible:

```python
def upgrade():
    # Forward change
    op.add_column('users', sa.Column('email', sa.String(255)))

def downgrade():
    # Exact reversal
    op.drop_column('users', 'email')
```

### 6.3 Backup Strategy

**Recommended**: Always backup before applying migrations to production

```bash
# Full database backup
pg_dump $DATABASE_URL > backup_$(date +%s).sql

# Or use helper script
./migrate.sh backup

# Then apply
alembic upgrade head
```

---

## 7. Deployment Specifications

### 7.1 Development Environment

```bash
# 1. Load .env file
export $(cat .env | xargs)

# 2. Apply migrations
alembic upgrade head

# 3. Verify
alembic current
```

### 7.2 Staging Environment

```bash
# 1. Set database URL
export DATABASE_URL="postgresql://staging_user:pass@staging-db:5432/fover_staging"

# 2. Backup
pg_dump $DATABASE_URL > backup_staging_$(date +%s).sql

# 3. Apply
alembic upgrade head

# 4. Test application
pytest tests/

# 5. Verify schema
alembic current
```

### 7.3 Production Environment

```bash
# 1. Set database URL (from secret manager)
export DATABASE_URL=$(aws secretsmanager get-secret-value --secret-id db-url --query SecretString --output text)

# 2. Backup
pg_dump $DATABASE_URL > /backups/backup_prod_$(date +%Y%m%d_%H%M%S).sql

# 3. Test (generate SQL without applying)
alembic upgrade head --sql | head -50

# 4. Apply with confirmation
alembic upgrade head

# 5. Verify and rollback plan
alembic current
echo "To rollback: alembic downgrade -1"
```

### 7.4 Kubernetes Deployment

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: database-migrations
spec:
  template:
    spec:
      containers:
      - name: alembic
        image: your-registry/fover-backend:latest
        command:
          - /bin/bash
          - -c
          - |
            set -e
            echo "Starting database migrations..."
            alembic upgrade head
            echo "Migrations complete"
            alembic current
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: database-credentials
              key: url
      restartPolicy: Never
```

---

## 8. Error Handling Specifications

### 8.1 Common Error Scenarios

| Error | Cause | Resolution |
|-------|-------|-----------|
| `No changes detected` | Model not imported | Add import to env.py and __init__.py |
| `Can't drop column - FK` | Foreign key constraint | Drop FK first, then column |
| `Version not found` | Missing migration file | Restore from git or recreate |
| `Connection refused` | Database offline | Check DATABASE_URL, start DB |
| `Column already exists` | Migration applied twice | Check alembic_version table |

### 8.2 Recovery Procedures

**Migration Applied Incorrectly**:
```bash
# 1. Rollback
alembic downgrade -1

# 2. Check schema
alembic current

# 3. Fix migration
vi alembic/versions/{id}_{name}.py

# 4. Reapply
alembic upgrade head
```

**Database Corrupted**:
```bash
# 1. Restore from backup
psql $DATABASE_URL < backup.sql

# 2. Check current version
alembic current

# 3. Apply any missing migrations
alembic upgrade head
```

---

## 9. Monitoring & Auditing Specifications

### 9.1 Migration Tracking

**Tracking Table**: `alembic_version` (auto-created)

```sql
SELECT * FROM alembic_version;
-- Shows current migration revision
```

### 9.2 Audit Logging

All migrations are logged:
```python
# env.py logs:
# - DATABASE_URL (source: env var or ini)
# - Current schema changes
# - Comparison results
# - SQL execution (in online mode)
```

### 9.3 Status Checks

```bash
# Current version
alembic current

# Latest available
alembic heads

# Full history
alembic history --verbose

# Specific revision details
alembic history --verbose -r {revision_id}
```

---

## 10. Performance Specifications

### 10.1 Benchmark Targets

| Operation | Target Time | Constraint |
|-----------|------------|-----------|
| Generate migration | < 5 seconds | Connection required |
| Apply single migration | < 30 seconds | Depends on schema changes |
| Apply 10 migrations | < 5 minutes | Large tables may take longer |
| Autogenerate comparison | < 10 seconds | Full schema comparison |

### 10.2 Optimization Notes

- Offline mode (`--sql`) skips actual execution (fast)
- Online mode executes migrations (slower, immediate)
- Connection pooling reduces overhead
- Batch processing for large alterations

---

## 11. Versioning Specifications

### 11.1 Revision ID Format

**Alphanumeric** (current):
```
0abc1234567f_add_users_table.py
```

**Numeric** (legacy):
```
0001_initial.py
```

Both formats supported simultaneously.

### 11.2 Branching Specifications

For parallel development:

```python
# Migration created from branch A
revision = 'abc1234567f'
down_revision = 'def9876543e'
branch_labels = ('feature-a',)

# Migration created from branch B  
revision = 'xyz7654321d'
down_revision = 'def9876543e'
branch_labels = ('feature-b',)

# Merge point
revision = 'merge123456'
down_revision = ('abc1234567f', 'xyz7654321d')
branch_labels = None

# After merge: alembic upgrade head
```

---

## 12. Integration with CI/CD

### 12.1 GitHub Actions Example

```yaml
- name: Run Database Migrations
  env:
    DATABASE_URL: ${{ secrets.DATABASE_URL }}
  run: |
    alembic upgrade head
```

### 12.2 Pre-Deployment Checks

```bash
#!/bin/bash
set -e

# 1. Verify all models are imported
grep -q "from app.models import" alembic/env.py

# 2. Check for empty migrations
find alembic/versions -name "*.py" -type f | while read f; do
    if grep -q "pass" "$f"; then
        echo "Empty migration found: $f"
        exit 1
    fi
done

# 3. Verify migration chain
alembic history --verbose > /dev/null

echo "All checks passed!"
```

---

## 13. Future Enhancements

- [ ] Add automatic rollback capability
- [ ] Implement migration approval workflow
- [ ] Add schema validation tests
- [ ] Create migration performance analyzer
- [ ] Add conflict detection for parallel development

---

## Reference

**Configuration Files**:
- `alembic/env.py` - Primary configuration
- `alembic.ini` - Database URL setup
- `app/models/__init__.py` - Model registry
- `app/db/__init__.py` - Base class definition

**Documentation Files**:
- [MIGRATIONS.md](MIGRATIONS.md) - Complete user guide
- [MIGRATIONS_IMPLEMENTATION.md](MIGRATIONS_IMPLEMENTATION.md) - Implementation details

**Helper Scripts**:
- `migrate.sh` - Linux/Mac helper
- `migrate.bat` - Windows helper

---

**Document Version**: 1.0  
**Last Updated**: May 31, 2026  
**Alembic**: 1.18.4  
**SQLAlchemy**: 2.0+  
**PostgreSQL**: 12+  
