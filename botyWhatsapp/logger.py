import logging as log
import sys

log.basicConfig(
    level=log.DEBUG,
    format='%(levelname)s -> %(message)s <- %(filename)s | %(lineno)s',
    handlers=[
        log.StreamHandler(sys.stdout)
    ]
)