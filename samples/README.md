[Uploading README.md…]()
# samples/

Real job output downloaded from z/OS with Zowe CLI (read-only):

    zowe zos-jobs list jobs --prefix "PAY*" --rff jobid jobname retcode --rft table
    zowe zos-jobs download output JOBxxxxx -d samples/

Remove your user ID from the files before committing.
