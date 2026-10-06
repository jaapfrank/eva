$ErrorActionPreference = 'Stop'
& wsl.exe -d Ubuntu -u administrator -- systemctl --user start eva2-project-dashboard.service
if ($LASTEXITCODE -ne 0) {
    throw 'The existing WSL project dashboard could not start.'
}
