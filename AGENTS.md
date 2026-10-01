# AI Agent Guidelines for FunForge

## 🚨 CRITICAL SYSTEM OPERATION RULES 🚨

### FORBIDDEN OPERATIONS - NEVER DO THESE

These operations have caused catastrophic data loss in the past. **NEVER** perform these without explicit user permission:

#### ⛔ System-Level Operations
- `sudo reboot` - NEVER restart the entire system
- `systemctl reboot` - NEVER restart the entire system
- `shutdown` - NEVER shutdown the system
- `poweroff` - NEVER power off the system
- `init 6` - NEVER restart via init

#### ⛔ Database Operations
- `pg_resetwal` - NEVER use PostgreSQL recovery tools without verified backups
- `DROP DATABASE` - NEVER drop databases without explicit permission
- Direct database file deletion - NEVER delete database files
- `TRUNCATE` on production tables - NEVER truncate without permission

#### ⛔ Container Operations
- `docker-compose down` on production - NEVER stop all services at once
- `docker stop $(docker ps -aq)` - NEVER stop all containers
- `docker system prune -a` - NEVER prune without permission
- Deleting volumes without backup - NEVER delete data volumes

### ✅ CORRECT PROCEDURES

#### Restarting Services

**Use standard Docker Compose commands:**
```bash
cd /path/to/project
docker compose restart <service>

# Or to rebuild and redeploy:
docker compose up -d --build
```


**METHODS THAT DON'T WORK ON THIS SERVER:**
```bash
# These FAIL with "permission denied":
docker restart <container>          # ❌ FAILS
docker-compose restart <service>    # ❌ FAILS
sudo docker restart <container>     # ❌ FAILS
docker-compose down                 # ❌ FAILS (can't stop)

# NEVER do this:
sudo reboot                         # ❌ CATASTROPHIC
```

## Project Guidelines

### Development Workflow
1. Make changes in feature branches
2. Test locally before committing
3. Use docker-compose for local development
4. Keep production and dev environments separate

### Code Standards
- Python 3.8+ with type hints
- FastAPI for REST API
- SQLAlchemy for database ORM
- Pydantic for data validation
- Follow PEP 8 style guide

### Docker Usage
- Use `docker-compose.yml` for development and production
- Never mix dev and prod databases
- Always use volumes for persistent data

### Database Management
- SQLite for development and production
- Always backup before migrations
- Never edit database files directly



### iptables: Stale DNAT Rules After Container Redeploy

**Recurring issue**: When Docker containers are recreated, stale DNAT rules remain in
iptables and hijack traffic before the correct rule fires. The service will work on
`127.0.0.1` but be **unreachable externally** (e.g., via Tailscale `100.69.184.113`).

**Always check after a redeploy if a service is externally unreachable:**
```bash
sudo iptables-save | grep "DNAT.*<port>"
# If two rules exist for the same port — remove the stale one:
sudo iptables -t nat -D DOCKER ! -i <old-bridge> -p tcp -m tcp --dport <port> -j DNAT --to-destination <old-ip>:<port>
```
