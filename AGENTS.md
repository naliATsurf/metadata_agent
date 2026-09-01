# AGENTS.md

## Security boundaries

This workspace contains sensitive configuration and credentials that may provide access to external systems.

### Forbidden resources

The coding agent MUST NOT:

* Read, inspect, print, copy, summarize, modify, or transmit secrets, credentials, tokens, certificates, private keys, or connection strings.
* Access configuration files containing credentials unless they are explicitly required for the current coding task and explicitly approved by the user.
* Use credentials found in the workspace to authenticate to any external service.
* Connect to production, staging, internal, corporate, customer, or otherwise protected infrastructure.
* Send network requests to services discovered from configuration files, environment variables, shell history, credential stores, or source code.
* Execute commands whose purpose is to discover, enumerate, test, or validate available credentials.
* Use tools such as `curl`, `wget`, `ssh`, database clients, cloud CLIs, Kubernetes clients, or similar tools to contact protected infrastructure.
* Modify firewall, VPN, proxy, authentication, credential, or security settings.
* Upload workspace files, logs, configuration, or source code to external services unless explicitly requested.

### Sensitive files and locations

Treat the following as sensitive and do not inspect their contents:

Do not read, summarize, edit, search, or use:

- .env
- config.ini.*s
- .env.*
- secrets/**
- credentials/**
- private/**
- *.pem
- *.key
- *.p12s
- *.pfx
- *.sqlite
- *.db
- SSH configuration and keys
- cloud-provider credential directories
- Kubernetes configuration files
- credential stores
- files whose names indicate secrets, credentials, tokens, or production configuration

If a task appears to require information from one of these resources, stop and ask the user rather than reading it.

### Environment variables

Do not enumerate the complete environment.

Do not run commands such as:

```bash
env
printenv
set
export
```

when they could expose credentials.

Only inspect explicitly named, non-sensitive environment variables necessary for the task.

### Network access

Assume network access is prohibited unless the user explicitly authorizes a specific destination for the current task.

Authorization for one host or service does not authorize access to any other host or service.

Never infer permission from the presence of credentials, URLs, configuration files, CLI profiles, or reachable infrastructure.

### Command execution

Before running a command, consider whether it could:

1. expose sensitive information;
2. contact an external or internal system;
3. use credentials implicitly;
4. change infrastructure or remote state.

If any of these are possible and the operation has not been explicitly authorized, do not execute the command.

### Testing

Prefer:

- unit tests;
- mocks;
- fixtures;
- local containers;
- local development servers;
- fake credentials;
- isolated test databases.

Do not use real infrastructure simply because configuration for it is available.

### Least privilege

Use the minimum files, commands, permissions, and network access necessary to complete the requested coding task.

The presence of access does not imply permission to use that access.
