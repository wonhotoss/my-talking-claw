"""Test text for the tts probe.

Two sets. `by_length` is what the latency columns run on: replies shaped like
the ones the brain actually produces, in the three sizes the sentence-level
pre-output hands to /speak. `by_vowel` isolates one Korean vowel per line so
the viseme-ID time share of that line says which Azure ID the vowel lands on
- the mapping table tts/app/viseme.py will need for an `azure_visemes` entry.

Syllable lists rather than prose for the vowel set: a natural sentence mixes
vowels and the histogram would say nothing. The consonants vary across the
list on purpose so the vowel is the only thing every syllable shares.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class sample:
    label: str
    text: str


by_length = [
    sample(label="short", text="네, 알겠어요."),
    sample(label="short", text="지금 찾아볼게요."),
    sample(label="medium", text="오늘 서울 날씨는 맑고 최고 기온은 18도예요."),
    sample(label="medium", text="두 시간 뒤에 알려 드릴게요. 그때 다시 불러 주세요."),
    sample(
        label="long",
        text="지금 바로 확인해 볼게요. 조금만 기다려 주시면 결과를 정리해서 말씀드릴게요. 다른 궁금한 것도 같이 물어보셔도 돼요.",
    ),
]

by_vowel = [
    sample(label="ㅏ", text="아, 가, 나, 다, 라, 사, 자, 하"),
    sample(label="ㅐ", text="애, 개, 내, 대, 래, 새, 재, 해"),
    sample(label="ㅔ", text="에, 게, 네, 데, 레, 세, 제, 헤"),
    sample(label="ㅓ", text="어, 거, 너, 더, 러, 서, 저, 허"),
    sample(label="ㅗ", text="오, 고, 노, 도, 로, 소, 조, 호"),
    sample(label="ㅜ", text="우, 구, 누, 두, 루, 수, 주, 후"),
    sample(label="ㅡ", text="으, 그, 느, 드, 르, 스, 즈, 흐"),
    sample(label="ㅣ", text="이, 기, 니, 디, 리, 시, 지, 히"),
    # The one consonant class the face draws on its own (viseme "m"): every
    # syllable here should put ID 21 on the histogram.
    sample(label="ㅁㅂㅍ", text="마, 바, 파, 뭐, 버, 퍼, 모, 보"),
]
