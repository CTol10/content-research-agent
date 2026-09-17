"""释放被 Playwright 抓取专用 Chrome 占用的 profile 锁（只杀绑定该 profile 的 chrome）。"""
import subprocess
import sys
import time

PROFILE_MARK = "_browser_profile"

PS = (
    "$procs = Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
    "Where-Object { $_.CommandLine -like '*" + PROFILE_MARK + "*' }; "
    "Write-Output ('found=' + @($procs).Count); "
    "foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }"
)


def main():
    out = subprocess.run(["powershell", "-NoProfile", "-Command", PS],
                         capture_output=True, text=True)
    print(out.stdout.strip() or out.stderr.strip())
    time.sleep(2)
    if "--check" in sys.argv:
        return


if __name__ == "__main__":
    main()
