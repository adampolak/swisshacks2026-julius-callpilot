param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8000,
    [string]$Python = "python",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = "http://${HostName}:$Port"

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class JobObject {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string lpName);

    [DllImport("kernel32.dll")]
    private static extern bool SetInformationJobObject(
        IntPtr hJob,
        int JobObjectInfoClass,
        IntPtr lpJobObjectInfo,
        uint cbJobObjectInfoLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr hObject);

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public long Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_COUNTERS {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    private const int JobObjectExtendedLimitInformation = 9;
    private const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;

    public static IntPtr CreateKillOnCloseJob() {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) {
            throw new System.ComponentModel.Win32Exception();
        }

        JOBOBJECT_EXTENDED_LIMIT_INFORMATION info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

        int length = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
        IntPtr infoPtr = Marshal.AllocHGlobal(length);
        try {
            Marshal.StructureToPtr(info, infoPtr, false);
            if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, infoPtr, (uint)length)) {
                throw new System.ComponentModel.Win32Exception();
            }
        } finally {
            Marshal.FreeHGlobal(infoPtr);
        }

        return job;
    }

    public static void AssignProcess(IntPtr job, IntPtr process) {
        if (!AssignProcessToJobObject(job, process)) {
            throw new System.ComponentModel.Win32Exception();
        }
    }

    public static void CloseJob(IntPtr job) {
        if (job != IntPtr.Zero) {
            CloseHandle(job);
        }
    }
}
"@

$processInfo = New-Object System.Diagnostics.ProcessStartInfo
$processInfo.FileName = $Python
$processInfo.Arguments = "server.py --host $HostName --port $Port"
$processInfo.WorkingDirectory = $scriptDir
$processInfo.UseShellExecute = $false
$processInfo.RedirectStandardOutput = $false
$processInfo.RedirectStandardError = $false

$serverProcess = New-Object System.Diagnostics.Process
$serverProcess.StartInfo = $processInfo
$jobHandle = [IntPtr]::Zero

try {
    $jobHandle = [JobObject]::CreateKillOnCloseJob()

    Write-Host "Starting server: $Python $($processInfo.Arguments)"
    if (-not $serverProcess.Start()) {
        throw "Failed to start server."
    }

    try {
        [JobObject]::AssignProcess($jobHandle, $serverProcess.Handle)
    } catch {
        Write-Warning "Could not attach server to a Windows cleanup job. Ctrl+C cleanup will still run."
    }

    $ready = $false
    for ($attempt = 1; $attempt -le 50; $attempt++) {
        if ($serverProcess.HasExited) {
            throw "Server exited early with code $($serverProcess.ExitCode)."
        }

        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                $ready = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 200
        }
    }

    if (-not $ready) {
        throw "Server did not become ready at $url."
    }

    if (-not $NoBrowser) {
        Write-Host "Opening browser: $url"
        Start-Process $url
    }
    Write-Host "Server is running. Press Ctrl+C or close this window to stop it."

    while (-not $serverProcess.HasExited) {
        Start-Sleep -Milliseconds 500
    }
} finally {
    if ($serverProcess -and -not $serverProcess.HasExited) {
        Write-Host "Stopping server..."
        $serverProcess.Kill()
        $serverProcess.WaitForExit()
    }

    if ($jobHandle -ne [IntPtr]::Zero) {
        [JobObject]::CloseJob($jobHandle)
    }
}
