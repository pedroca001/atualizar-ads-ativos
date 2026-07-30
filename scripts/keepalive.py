KEEPALIVE_INTERVAL_DAYS = 45
SECONDS_PER_DAY = 24 * 60 * 60


def keepalive_due(last_commit_epoch, now_epoch, interval_days=KEEPALIVE_INTERVAL_DAYS):
    return now_epoch - last_commit_epoch >= interval_days * SECONDS_PER_DAY


if __name__ == "__main__":
    import sys

    print(str(keepalive_due(int(sys.argv[1]), int(sys.argv[2]))).lower())
