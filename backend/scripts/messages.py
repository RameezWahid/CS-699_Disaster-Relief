"""Message templates for synthetic flood reports, shared by training data and scenario replay.

Every fourth template of each (style, class) is held out for replay, so replayed messages use
phrasings the classifier never saw in training. Templates need review by fluent speakers.
"""

import random

STYLES = ["en", "hinglish", "hi", "mr", "mr_latin"]
CLASSES = ["rescue", "medical", "food_water", "shelter", "road_blocked", "info_noise"]
REPLAY_EVERY = 4

# Headcount renderings per style: digit, word, or (Devanagari styles) Devanagari digit.
WORDS = {
    "en": {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 8: "eight", 10: "ten"},
    "hinglish": {2: "do", 3: "teen", 4: "char", 5: "paanch", 6: "chhe", 8: "aath", 10: "das"},
    "hi": {2: "दो", 3: "तीन", 4: "चार", 5: "पाँच", 6: "छह", 8: "आठ", 10: "दस"},
    "mr": {2: "दोन", 3: "तीन", 4: "चार", 5: "पाच", 6: "सहा", 8: "आठ", 10: "दहा"},
    "mr_latin": {2: "don", 3: "teen", 4: "char", 5: "pach", 6: "saha", 8: "aath", 10: "daha"},
}
DEVANAGARI_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")
PEOPLE = {"en": "people", "hinglish": "log", "hi": "लोग", "mr": "जण", "mr_latin": "jan"}
VULNERABLE = {
    "en": [
        "a small baby",
        "my old grandmother",
        "a pregnant lady",
        "an elderly man who can't walk",
        "a child",
        "a dialysis patient",
    ],
    "hinglish": [
        "ek chhota baccha",
        "dadi jo chal nahi sakti",
        "ek pregnant aurat",
        "buzurg uncle",
        "do bacche",
        "oxygen wale papa",
    ],
    "hi": ["एक छोटा बच्चा", "बूढ़ी दादी", "एक गर्भवती महिला", "बुज़ुर्ग चाचा", "दो बच्चे"],
    "mr": ["एक लहान बाळ", "म्हातारी आजी", "एक गरोदर बाई", "आजोबा", "दोन लहान मुलं"],
    "mr_latin": ["ek lahan bal", "mhatari aaji", "ek garodar bai", "ajoba", "don lahan mula"],
}

# {p}=place, {n}=headcount phrase, {v}=vulnerable person. Kept short and varied on purpose:
# real messages are terse. One template list per (style, class).
TEMPLATES = {
    ("en", "rescue"): [
        "{n} stuck on the roof near {p}, water rising, send a boat",
        "trapped in our building at {p}, ground floor gone, {n} inside incl {v}",
        "HELP car stuck in water at {p} {n} inside",
        "please rescue us {p} water up to the neck, {v} with us",
        "we are {n} on the terrace at {p}, cant get down, need rescue",
        "{v} stuck at home {p}, water entering fast please evacuate",
        "bus stalled in floodwater near {p}, passengers trapped",
        "someone is drowning near {p} send help now",
    ],
    ("en", "medical"): [
        "need an ambulance at {p}, {v} is very sick",
        "{v} needs medicine urgently near {p}, roads flooded",
        "man injured badly near {p}, bleeding, need a doctor",
        "oxygen cylinder running out at {p}, please help",
        "dialysis patient at {p} cannot reach hospital",
        "child with high fever at {p}, no doctor available",
        "snake bite near {p}, need hospital",
        "any doctor near {p}? {n} have vomiting and loose motions",
    ],
    ("en", "food_water"): [
        "no food since yesterday at {p}, {n} here",
        "need drinking water at {p}, {n} people",
        "please send food packets to {p}",
        "milk for {v} needed near {p}, shops closed",
        "ration finished at {p}, {n} hungry",
        "water tanker needed at {p}, no drinking water for 2 days",
    ],
    ("en", "shelter"): [
        "our house collapsed at {p}, {n} need a place to stay",
        "home flooded at {p}, where is the nearest relief camp",
        "need shelter for {n} tonight near {p}",
        "building declared unsafe at {p}, {v} with us, need a safe place",
        "need blankets and tarpaulin at {p}, roof gone",
        "is any school open as shelter near {p}? we are homeless",
    ],
    ("en", "road_blocked"): [
        "road closed at {p}, water knee deep, avoid",
        "{p} subway flooded, traffic diverted",
        "tree fallen at {p}, road blocked, no one hurt",
        "huge traffic jam near {p} due to waterlogging",
        "tracks submerged near {p}, trains stopped",
        "avoid {p}, vehicles not moving",
    ],
    ("en", "info_noise"): [
        "heavy rain in {p} today, stay safe everyone",
        "is {p} station flooded? need to go to office",
        "forwarded: dam will break tonight, evacuate {p}!! share",
        "lol {p} looks like venice today",
        "IMD orange alert for Mumbai tomorrow",
        "schools closed tomorrow as per BMC",
        "pray for Mumbai 🙏",
        "new flats in {p}, special monsoon offer, call now",
        "local trains running late due to rain",
    ],
    ("hinglish", "rescue"): [
        "{p} pe {n} phase hain, paani badh raha hai, boat bhejo",
        "bhai {p} me gaadi paani me band, {n} andar hain",
        "humko bachao {p} me, ghar me gale tak paani, {v} bhi hai",
        "{p} ke paas chhat pe {n} hain, neeche paani hi paani",
        "jaldi rescue bhejo {p}, {v} phasi hai",
        "{p} me building ka basement bhar gaya, log fase hain",
        "koi doob raha hai {p} ke nalle me, jaldi aao",
        "{n} {p} me phas gaye, nikalo plz",
    ],
    ("hinglish", "medical"): [
        "{p} me ambulance chahiye, {v} ki tabiyat kharab hai",
        "{v} ki dawai khatam ho gayi {p} me, road band hai",
        "{p} ke paas ek aadmi ko chot lagi hai, khoon beh raha hai",
        "oxygen cylinder khatam hone wala hai {p} me",
        "bacche ko tez bukhar hai {p} me, koi doctor?",
        "{p} me dialysis ke liye jana hai, gaadi nahi",
        "saanp ne kaata {p} me, hospital le jana hai",
        "{p} me {n} hain, sabko ulti dast ho rahe, doctor bhejo",
    ],
    ("hinglish", "food_water"): [
        "{p} me khana nahi hai kal se, {n} hain",
        "peene ka paani chahiye {p} me, {n}",
        "{p} me ration khatam, kuch bhejo",
        "{v} ke liye doodh chahiye {p} me",
        "khane ke packet bhejo {p}, bacche bhookhe hain",
        "{p} me 2 din se pine ka pani nahi",
    ],
    ("hinglish", "shelter"): [
        "ghar gir gaya {p} me, hum {n} hain, rehne ki jagah chahiye",
        "{p} me ghar me paani bhar gaya, kahan jaye",
        "raat ko rukne ki jagah chahiye {p} ke paas, {n} log",
        "{p} me jhopdi baha gayi, tarpaulin aur blanket chahiye",
        "koi relief camp hai {p} ke paas? {v} saath me hai",
        "{p} me building unsafe hai, temporary jagah chahiye",
    ],
    ("hinglish", "road_blocked"): [
        "{p} pe road band hai, paani bhara hai",
        "{p} subway me paani, mat jao",
        "{p} pe ped gir gaya, rasta band, koi ghayal nahi",
        "{p} ke paas bahut traffic, gaadiya ruki hui",
        "{p} me track pe paani, train band",
        "{p} se mat jao, road pe 3 feet paani",
    ],
    ("hinglish", "info_noise"): [
        "{p} me aaj bahut barish ho rahi hai",
        "kya {p} station pe train chal rahi hai?",
        "fwd: kal dam tootega, {p} khali karo, sabko bhejo",
        "{p} toh Venice ban gaya 😂",
        "kal school band hai kya?",
        "Ganpati bappa sabko bachana 🙏",
        "{p} me naya flat, barish offer, call karo",
        "kal office jana hai ya WFH?",
        "IMD ne red alert diya hai, dhyan rakho",
    ],
    ("hi", "rescue"): [
        "{p} में {n} फँसे हैं, पानी बढ़ रहा है, नाव भेजो",
        "{p} में गाड़ी पानी में बंद, अंदर {n} हैं",
        "हमें बचाओ, {p} में घर में गले तक पानी, साथ में {v}",
        "{p} के पास छत पर {n} हैं, नीचे पानी ही पानी",
        "{p} में कोई डूब रहा है, जल्दी आओ",
        "{p} की बिल्डिंग के बेसमेंट में लोग फँसे हैं",
    ],
    ("hi", "medical"): [
        "{p} में एम्बुलेंस चाहिए, {v} की तबीयत खराब है",
        "{v} की दवा खत्म हो गई {p} में, सड़क बंद है",
        "{p} के पास एक आदमी घायल है, खून बह रहा है",
        "{p} में ऑक्सीजन सिलेंडर खत्म होने वाला है",
        "बच्चे को तेज़ बुखार है {p} में, कोई डॉक्टर?",
        "{p} में डायलिसिस के लिए जाना है, गाड़ी नहीं",
    ],
    ("hi", "food_water"): [
        "{p} में कल से खाना नहीं है, {n} हैं",
        "{p} में पीने का पानी चाहिए, {n}",
        "{p} में राशन खत्म, कुछ भेजो",
        "{v} के लिए दूध चाहिए {p} में",
        "{p} में खाने के पैकेट भेजिए",
    ],
    ("hi", "shelter"): [
        "{p} में घर गिर गया, हम {n} हैं, रहने की जगह चाहिए",
        "{p} में घर में पानी भर गया, कहाँ जाएँ",
        "{p} के पास रात को रुकने की जगह चाहिए, {n}",
        "{p} में झोपड़ी बह गई, तिरपाल और कंबल चाहिए",
        "{p} के पास कोई राहत शिविर है? साथ में {v}",
    ],
    ("hi", "road_blocked"): [
        "{p} पर सड़क बंद है, पानी भरा है",
        "{p} सबवे में पानी, मत जाओ",
        "{p} पर पेड़ गिरा, रास्ता बंद, कोई घायल नहीं",
        "{p} के पास भारी ट्रैफिक, गाड़ियाँ रुकी हैं",
        "{p} में पटरियों पर पानी, लोकल बंद",
    ],
    ("hi", "info_noise"): [
        "{p} में आज बहुत बारिश हो रही है",
        "क्या {p} स्टेशन पर ट्रेन चल रही है?",
        "फॉरवर्ड: कल बाँध टूटेगा, {p} खाली करो",
        "{p} तो वेनिस बन गया 😂",
        "कल स्कूल बंद है क्या?",
        "हे भगवान, बारिश रोक दो 🙏",
        "मौसम विभाग ने ऑरेंज अलर्ट दिया है",
    ],
    ("mr", "rescue"): [
        "{p} येथे {n} अडकलो आहोत, पाणी वाढतंय, बोट पाठवा",
        "{p} मध्ये गाडी पाण्यात बंद, आत {n} आहेत",
        "आम्हाला वाचवा, {p} येथे घरात गळ्यापर्यंत पाणी, सोबत {v}",
        "{p} जवळ छतावर {n} आहोत, खाली सगळं पाणी",
        "{p} येथे कोणी बुडतंय, लवकर या",
        "{p} च्या इमारतीच्या तळघरात लोक अडकले आहेत",
    ],
    ("mr", "medical"): [
        "{p} येथे रुग्णवाहिका हवी, {v} ची तब्येत बिघडली",
        "{v} चं औषध संपलं {p} मध्ये, रस्ता बंद आहे",
        "{p} जवळ एक माणूस जखमी आहे, रक्त वाहतंय",
        "{p} मध्ये ऑक्सिजन सिलेंडर संपत आलाय",
        "बाळाला खूप ताप आहे {p} येथे, कोणी डॉक्टर?",
        "{p} येथे डायलिसिससाठी जायचंय, गाडी नाही",
    ],
    ("mr", "food_water"): [
        "{p} येथे कालपासून जेवण नाही, {n} आहोत",
        "{p} मध्ये प्यायचं पाणी हवं, {n}",
        "{p} येथे शिधा संपला, काहीतरी पाठवा",
        "{v} साठी दूध हवं {p} मध्ये",
        "{p} येथे जेवणाची पाकिटं पाठवा",
    ],
    ("mr", "shelter"): [
        "{p} येथे घर कोसळलं, आम्ही {n} आहोत, राहायला जागा हवी",
        "{p} मध्ये घरात पाणी शिरलं, कुठे जायचं",
        "{p} जवळ रात्री थांबायला जागा हवी, {n}",
        "{p} येथे झोपडी वाहून गेली, ताडपत्री आणि घोंगड्या हव्या",
        "{p} जवळ मदत शिबिर आहे का? सोबत {v}",
    ],
    ("mr", "road_blocked"): [
        "{p} येथे रस्ता बंद, पाणी साचलंय",
        "{p} सबवेमध्ये पाणी, जाऊ नका",
        "{p} येथे झाड पडलं, रस्ता बंद, कोणी जखमी नाही",
        "{p} जवळ खूप ट्रॅफिक, वाहनं थांबली",
        "{p} येथे रुळांवर पाणी, लोकल बंद",
    ],
    ("mr", "info_noise"): [
        "{p} मध्ये आज खूप पाऊस पडतोय",
        "{p} स्टेशनवर गाड्या चालू आहेत का?",
        "फॉरवर्ड: उद्या धरण फुटणार, {p} रिकामं करा",
        "{p} चं तर व्हेनिस झालं 😂",
        "उद्या शाळा बंद आहे का?",
        "गणपती बाप्पा सगळ्यांना सांभाळ 🙏",
        "हवामान विभागाने रेड अलर्ट दिला आहे",
    ],
    ("mr_latin", "rescue"): [
        "{p} la {n} adaklo aahot, pani vadhtay, boat pathva",
        "{p} madhe gaadi panyat band, aat {n} aahet",
        "amhala vachva, {p} la gharat galyaparyant pani, sobat {v}",
        "{p} javal chhatavar {n} aahot, khali sagla pani",
        "{p} la koni budtay, lavkar ya",
        "{p} chya building chya basement madhe lok adakle",
    ],
    ("mr_latin", "medical"): [
        "{p} la ambulance pahije, {v} chi tabyet bighadli",
        "{v} che aushadh sampla {p} madhe, rasta band",
        "{p} javal ek manus jakhmi aahe, rakt vahtay",
        "{p} madhe oxygen cylinder sampat aala",
        "balala khoop taap aahe {p} la, koni doctor?",
    ],
    ("mr_latin", "food_water"): [
        "{p} la kalpasun jevan nahi, {n} aahot",
        "{p} madhe pinyache pani pahije, {n}",
        "{p} la shidha sampla, kahitari pathva",
        "{v} sathi dudh pahije {p} madhe",
    ],
    ("mr_latin", "shelter"): [
        "{p} la ghar kosalle, amhi {n} aahot, rahayla jaga pahije",
        "{p} madhe gharat pani shirle, kuthe jaycha",
        "{p} javal ratri thambayla jaga pahije, {n}",
        "{p} la zopdi vahun geli, tadpatri ani blanket pahije",
    ],
    ("mr_latin", "road_blocked"): [
        "{p} la rasta band, pani saachle aahe",
        "{p} subway madhe pani, jau naka",
        "{p} la zhad padle, rasta band, koni jakhmi nahi",
        "{p} javal khoop traffic, vahane thambli",
    ],
    ("mr_latin", "info_noise"): [
        "{p} madhe aaj khoop paus padtoy",
        "{p} station var gadya chalu aahet ka?",
        "forward: udya dharan futnar, {p} rikama kara",
        "{p} cha tar Venice jhala 😂",
        "udya shala band aahe ka?",
        "Ganpati Bappa sarvanna sambhal 🙏",
    ],
}
SMS = {"please": "pls", "people": "ppl", "jaldi": "jldi", "nahi": "nhi", "help": "hlp"}


def headcount_phrase(style: str, rng: random.Random) -> tuple[str, int]:
    n = rng.choice([2, 3, 4, 5, 6, 8, 10, 12, 15, 20])
    form = rng.random()
    if n in WORDS[style] and form < 0.35:
        number = WORDS[style][n]
    elif style in ("hi", "mr") and form < 0.6:
        number = str(n).translate(DEVANAGARI_DIGITS)
    else:
        number = str(n)
    return f"{number} {PEOPLE[style]}", n


def add_noise(text: str, style: str, rng: random.Random) -> str:
    """Phone-typing noise: shortcuts, dropped punctuation, lowercase, emphasis, a typo."""
    if style in ("en", "hinglish", "mr_latin"):
        if rng.random() < 0.4:
            text = " ".join(SMS.get(w, w) for w in text.split())
        if rng.random() < 0.3:
            text = text.lower()
        if rng.random() < 0.2:
            words = text.split()
            i = rng.randrange(len(words))
            if len(words[i]) > 4:
                j = rng.randrange(1, len(words[i]) - 1)
                words[i] = words[i][:j] + words[i][j + 1 :]  # dropped letter
            text = " ".join(words)
    if rng.random() < 0.3:
        text = text.replace(",", "")
    if rng.random() < 0.15:
        text += rng.choice(["!!", "!!!", " 🙏", " plz", " urgent"])
    return text


def templates_for(style: str, label: str, part: str) -> list[str]:
    """Templates for "train" or "replay"; the two sets never overlap."""
    templates = TEMPLATES[(style, label)]
    held_out = {i for i in range(len(templates)) if i % REPLAY_EVERY == REPLAY_EVERY - 1}
    return [t for i, t in enumerate(templates) if (i in held_out) == (part == "replay")]
