include:
  - nginx.config
  - nginx.service
  - monitoring.agent

nginx-package:
  pkg.installed:
    - name: nginx
