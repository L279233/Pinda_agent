from enum import Enum

class InterviewStage(str, Enum):       # 继承 str，取值就是字符串
    WARMUP    = "warmup"
    TECH_BASE = "tech_base"
    PROJECT   = "project"
    CLOSING   = "closing"
    FINISHED  = "finished"

print(InterviewStage.WARMUP)           # InterviewStage.WARMUP
print(InterviewStage.WARMUP.value)     # warmup
sate = InterviewStage("warmup")
print(sate)
