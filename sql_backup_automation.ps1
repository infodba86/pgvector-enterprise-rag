# SQL Server Backup Automation Script
# Author: Suresh Nadipineni | Senior DBA & AI Data Engineer

param(
    [string]$ServerInstance = "localhost",
    [string]$BackupPath = "C:\SQLBackups",
    [int]$RetentionDays = 7
)

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"

function Write-Log {
    param([string]$Message)
    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
}

Write-Log "Starting SQL Server Backup on $ServerInstance"

$databases = Invoke-Sqlcmd -ServerInstance $ServerInstance `
    -Query "SELECT name FROM sys.databases WHERE database_id > 4 AND state_desc = 'ONLINE'"

foreach ($db in $databases) {
    $dbName = $db.name
    $backupFile = "$BackupPath\${dbName}_FULL_$timestamp.bak"
    Write-Log "Backing up: $dbName"
    Invoke-Sqlcmd -ServerInstance $ServerInstance -Query "
    BACKUP DATABASE [$dbName]
    TO DISK = N'$backupFile'
    WITH COMPRESSION, CHECKSUM, STATS = 10"
    Write-Log "SUCCESS: $dbName -> $backupFile"
}

# Cleanup old backups
Get-ChildItem -Path $BackupPath -Filter "*.bak" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$RetentionDays) } |
    Remove-Item -Force

Write-Log "Backup automation completed!"
