try:
    from .quiz_answers import resolve_answer
except ImportError:
    from quiz_answers import resolve_answer

AXES = ["Directness", "Reassurance", "Conflict comfort", "Independence", "Openness"]
DIMS = ["EI", "SN", "TF", "JP"]
VERSION = 3


def q(id_, text, axis, options, dim=None):
    out = {"id": id_, "q": text, "axis": axis,
           "options": [{"label": label, "tag": tag} for label, tag in options]}
    if dim:
        out["dim"] = dim
    return out


SHORT = [
    q("s1", "When something bothers you, you usually…", "Directness", [
        ("Say it straight away", "direct communicator"), ("Hint and hope they notice", "drops hints"),
        ("Wait until I've cooled down, then talk", "thinks before speaking"), ("Keep it to myself", "keeps things in")]),
    q("s2", "He hasn't replied in 6 hours. You…", "Reassurance", [
        ("Don't notice — I'm busy", "secure about replies"), ("Notice, but it's fine", "mostly relaxed"),
        ("Re-read the last message for clues", "overthinks silence"), ("Feel it in my stomach and want an answer", "needs reassurance")]),
    q("s3", "In an argument you tend to…", "Conflict comfort", [
        ("Stand my ground calmly", "stands firm"), ("Get heated, then make up fast", "fiery but forgiving"),
        ("Over-explain until they understand", "over-explains"), ("Go quiet and avoid it", "avoids conflict")]),
    q("s4", "What do you need most from the people close to you?", "Reassurance", [
        ("Consistency — do what you said", "values consistency"), ("Words — tell me how you feel", "needs to hear it"),
        ("Space and trust", "needs space"), ("Effort — show up and plan", "values effort")]),
    q("s5", "Your usual dating pattern is…", "Independence", [
        ("I fall fast and go all in", "falls fast"), ("I take ages to open up", "slow to open up"),
        ("I pick people who aren't fully available", "drawn to unavailable people"), ("Steady — I take it as it comes", "steady dater")]),
    q("s6", "When you like someone, you…", "Openness", [
        ("Tell them", "speaks openly about feelings"), ("Show it, but never say it first", "shows rather than tells"),
        ("Play it cool so I don't look keen", "plays it cool"), ("Wait for them to make every move", "waits to be chosen")]),
    q("s7", "Someone lets you down. You…", "Directness", [
        ("Tell them plainly what happened", "names the problem"), ("Forgive quickly, maybe too quickly", "forgives fast"),
        ("Pull back without saying why", "withdraws quietly"), ("Give one more chance, then I'm done", "clear limits")]),
    q("s8", "Your friends would say you're…", "Independence", [
        ("The one who gives advice, not takes it", "the advice-giver"), ("Loyal to a fault", "loyal to a fault"),
        ("Independent — I do my own thing", "independent"), ("Sensitive — I feel everything", "feels deeply")]),
    q("s9", "After a big week, you recharge by…", "Independence", [
        ("Going out with friends", "social battery"), ("A small dinner with one or two people", "small-circle person"),
        ("A quiet night in", "homebody"), ("Total alone time, phone off", "needs alone time")], "EI"),
    q("s10", "When you meet someone new, you notice…", "Openness", [
        ("The vibe and what they could become", "sees potential"), ("How they make me feel", "reads the energy"),
        ("What they actually say", "listens to words"), ("Facts — job, habits, how they treat the waiter", "notices the facts")], "SN"),
    q("s11", "Big decisions about people — you go with…", "Directness", [
        ("My heart, always", "leads with the heart"), ("Mostly feelings, checked with logic", "feeling-first"),
        ("Mostly logic, checked with feelings", "logic-first"), ("Cold hard facts", "decides through logic")], "TF"),
    q("s12", "Your ideal weekend plan is…", "Independence", [
        ("Planned to the hour", "a planner"), ("A rough plan", "likes a rough plan"),
        ("One thing booked, the rest open", "goes with the flow"), ("No plan, see what happens", "spontaneous")], "JP"),
]


def topics(rows):
    out = []
    for row in rows:
        id_, text, axis, opts = row[:4]
        item = {"id": id_, "q": text, "axis": axis, "note": True,
                "options": [{"label": o, "tag": o.lower()} for o in opts]}
        if len(row) > 4:
            item["dim"] = row[4]
        out.append(item)
    return out


EXTENDED = topics([
    ("e1", "When someone gets close, you feel…", "Reassurance", ["Calm and happy", "Happy but scared it won't last", "A bit trapped", "Unsure what they want from me"]),
    ("e2", "After a fight you need…", "Reassurance", ["Time alone first", "To talk it through right away", "A hug and no more words", "A clear apology"]),
    ("e3", "You feel most loved when someone…", "Openness", ["Says it out loud", "Spends real time with me", "Does things for me", "Is physically affectionate"]),
    ("e4", "You show love mostly by…", "Openness", ["Kind words", "Making time", "Doing favours", "Gifts and little surprises"]),
    ("e5", "What sets you off fastest?", "Conflict comfort", ["Being ignored", "Being lied to", "Being criticised", "Being controlled"]),
    ("e6", "Saying no to people is…", "Directness", ["Easy", "Fine, if I explain why", "Hard — I feel guilty", "Almost impossible"]),
    ("e7", "When a boundary is crossed, you…", "Directness", ["Say so the same day", "Let it go once, then say it", "Let it slide and resent it", "Cut them off"]),
    ("e8", "You trust people…", "Independence", ["Easily, until they prove otherwise", "Slowly, after time", "Only after they've been tested", "Rarely, fully"]),
    ("e9", "When you're stressed, you…", "Independence", ["Reach out to someone", "Go quiet and handle it", "Get busy to avoid it", "Get snappy"]),
    ("e10", "How much do you check your phone for one person?", "Reassurance", ["Barely", "A normal amount", "More than I'd admit", "Constantly"]),
    ("e11", "Your biggest fear in relationships is…", "Reassurance", ["Being left", "Losing myself", "Being bored", "Being taken for granted"]),
    ("e12", "You usually go for people who are…", "Openness", ["Kind and steady", "Exciting and unpredictable", "A project I can help", "Hard to read"]),
    ("e13", "When someone compliments you, you…", "Openness", ["Say thanks and enjoy it", "Brush it off", "Wonder what they want", "Compliment them back fast"]),
    ("e14", "In a group you are usually…", "Independence", ["The one talking", "The one listening", "The one organising", "Somewhere in between"]),
    ("e15", "How do you handle jealousy?", "Conflict comfort", ["Say it calmly", "Joke about it", "Hide it", "Check up on them"]),
    ("e16", "What does your family's way of arguing look like?", "Conflict comfort", ["Loud, then fine", "Silent treatment", "Calm talking", "We don't argue — we avoid"]),
    ("e17", "You apologise…", "Directness", ["When I'm wrong", "Even when I'm not wrong", "Rarely", "With actions, not words"]),
    ("e18", "What do you want to change most?", "Independence", ["Stop chasing", "Say what I need once", "Stop over-explaining", "Trust people more"]),
    ("e19", "When plans change last minute, you…", "Conflict comfort", ["Roll with it", "Feel annoyed but fine", "Take it personally", "Say it's not OK"]),
    ("e20", "Right now you most want…", "Openness", ["Something serious", "Something fun", "Clarity on what I have", "To focus on me"]),
    ("e21", "At a party you…", "Openness", ["Talk to everyone", "Stick with my group", "Find one deep chat", "Leave early"], "EI"),
    ("e22", "You trust…", "Openness", ["Gut feelings and patterns", "Mostly my gut", "Mostly what I've seen", "Only what I've seen happen"], "SN"),
    ("e23", "When a friend vents, you…", "Reassurance", ["Listen and comfort", "Comfort, then suggest", "Suggest, then comfort", "Fix it straight away"], "TF"),
    ("e24", "Texts you haven't answered right now…", "Directness", ["Zero — I reply fast", "A few", "Lots, I'll get to them", "Hundreds, oops"], "JP"),
    ("e25", "You think out loud…", "Directness", ["All the time", "Often", "Sometimes", "Never — I think first"], "EI"),
    ("e26", "You daydream about the future…", "Openness", ["Constantly", "Often", "Sometimes", "Rarely, I live in the now"], "SN"),
    ("e27", "Criticism from someone you love…", "Reassurance", ["Hurts for days", "Stings a while", "I take what's useful", "Doesn't faze me"], "TF"),
    ("e28", "Deadlines are…", "Independence", ["Met early", "Met on time", "Met at the last minute", "Suggestions"], "JP"),
    ("e29", "How often do you need to hear 'I like you'?", "Reassurance", ["Daily", "Every few days", "Now and then", "Never — actions matter"]),
    ("e30", "If he cancels a date last minute…", "Conflict comfort", ["I tell him it's not OK", "I say it's fine but it's not", "I cancel the next one", "Honestly fine"]),
    ("e31", "How fast do you share your past?", "Openness", ["First date", "After a few weeks", "After months", "Almost never"]),
    ("e32", "Your phone when you're with him…", "Independence", ["Away, fully present", "Mostly away", "Out but ignored", "Constantly on it"]),
    ("e33", "When you want more from the relationship, you…", "Directness", ["Ask directly", "Hint", "Wait for him", "Pretend I don't"]),
    ("e34", "He likes someone else's photo. You…", "Reassurance", ["Don't care", "Notice and move on", "It bothers me a bit", "It ruins my day"]),
    ("e35", "Your friends' opinion of him matters…", "Openness", ["A lot", "Somewhat", "A little", "Not at all"]),
    ("e36", "Your go-to in a cold war…", "Conflict comfort", ["Break it first", "Wait a day, then talk", "Wait for him", "Out-silence him"]),
])

FURTHER = topics([
    ("f1", "Your perfect date is…", "Openness", ["A busy bar or event", "Dinner somewhere lively", "A walk and a long talk", "Cooking at home"], "EI"),
    ("f2", "You'd rather he plans…", "Openness", ["Something new and surprising", "Something a bit different", "Somewhere we love", "Our usual place"], "SN"),
    ("f3", "When you argue, winning means…", "Conflict comfort", ["We both feel heard", "We feel close again", "We find the fix", "I was right"], "TF"),
    ("f4", "Moving in together — you'd…", "Independence", ["Plan it for months", "Plan the big bits", "Figure it out as we go", "Just do it"], "JP"),
    ("f5", "Group chats…", "Openness", ["I'm the most active", "I chat a fair bit", "I mostly read", "Muted"], "EI"),
    ("f6", "You remember…", "Openness", ["The feeling of a moment", "The meaning behind it", "The main events", "Exact details and dates"], "SN"),
    ("f7", "A friend is wrong. You…", "Directness", ["Protect her feelings", "Soften it a lot", "Tell her gently", "Tell her straight"], "TF"),
    ("f8", "Your room right now is…", "Independence", ["Spotless", "Tidy enough", "Organised chaos", "A disaster"], "JP"),
    ("f9", "First day at a new job, you…", "Openness", ["Meet everyone by lunch", "Chat with a few people", "Keep my head down", "Count the hours"], "EI"),
    ("f10", "You're more drawn to…", "Openness", ["Ideas and possibilities", "Stories and meaning", "Practical skills", "Facts and proof"], "SN"),
    ("f11", "Choosing between two guys, you'd…", "Directness", ["Pick who I feel more for", "Mostly go with feelings", "Mostly list pros and cons", "Pros and cons only"], "TF"),
    ("f12", "For a holiday you pack…", "Independence", ["A week early with a list", "The day before", "The morning of", "In the taxi"], "JP"),
    ("f13", "Silence on a date feels…", "Reassurance", ["Awkward — I fill it", "A bit uncomfortable", "Fine", "Lovely"], "EI"),
    ("f14", "Horoscopes and signs…", "Openness", ["I believe in them", "Fun and a bit true", "Just fun", "Nonsense"], "SN"),
    ("f15", "Someone cries in front of you…", "Reassurance", ["I cry too", "I hug them", "I ask what happened", "I feel awkward"], "TF"),
    ("f16", "Surprise plans from him…", "Independence", ["Stress me out", "Are fine with some notice", "Are fun", "Are the best thing ever"], "JP"),
    ("f17", "When he's out with friends you…", "Reassurance", ["Expect updates", "Like a text or two", "Hear about it later", "Don't think about it"]),
    ("f18", "If he takes hours to reply you assume…", "Reassurance", ["He's lost interest", "Something's off", "He's busy", "Nothing at all"]),
    ("f19", "Being single feels…", "Reassurance", ["Scary", "Lonely", "Fine", "Great"]),
    ("f20", "You re-read your own sent texts…", "Reassurance", ["Every time", "Often", "Sometimes", "Never"]),
    ("f21", "The compliments you need most are about…", "Reassurance", ["How much I matter", "How I look", "What I do", "I don't need them"]),
    ("f22", "When things are good, you…", "Reassurance", ["Wait for it to go wrong", "Feel lucky", "Enjoy it", "Don't notice"]),
    ("f23", "The love you give most is…", "Openness", ["Words", "Time", "Help", "Touch"]),
    ("f24", "You bring up old fights…", "Conflict comfort", ["Never", "Rarely", "Sometimes", "Often"]),
    ("f25", "If his family or friends dislike you…", "Conflict comfort", ["Talk to him about it", "Win them over", "Avoid them", "Pretend it's fine"]),
    ("f26", "Saying sorry first is…", "Conflict comfort", ["Easy", "OK", "Hard", "Something I don't do"]),
    ("f27", "Raised voices in an argument…", "Conflict comfort", ["Never happen", "Rarely", "Sometimes", "Often"]),
    ("f28", "When you're angry, you text…", "Conflict comfort", ["Calmly, later", "Right away but measured", "Right away, a lot", "Nothing — I block"]),
    ("f29", "Your family handled feelings by…", "Conflict comfort", ["Talking openly", "Some talking", "Moving on", "Ignoring them"]),
    ("f30", "On dates you're the one who…", "Directness", ["Suggests plans", "Shares plans", "Goes along", "Waits to be asked"]),
    ("f31", "You tell a guy you're not interested…", "Directness", ["Kindly and clearly", "With an excuse", "By fading out", "By ghosting"]),
    ("f32", "When a friend crosses a line…", "Directness", ["I tell her", "I hint", "I distance myself", "I let it go"]),
    ("f33", "Asking for help is…", "Directness", ["Easy", "Fine", "Uncomfortable", "Almost impossible"]),
    ("f34", "If you want to define the relationship you…", "Directness", ["Ask him", "Hint until he asks", "Wait for him", "Never ask"]),
    ("f35", "At work you speak up…", "Directness", ["Always", "Usually", "Sometimes", "Rarely"]),
    ("f36", "Your own goals in a relationship…", "Independence", ["Stay first", "Share the spotlight", "Come second", "Get paused"]),
    ("f37", "Weekends without him…", "Independence", ["I love them", "I enjoy them", "Feel odd", "Feel empty"]),
    ("f38", "Money in a couple should be…", "Independence", ["Fully separate", "Mostly separate", "Mostly shared", "All shared"]),
    ("f39", "Your friends after you start dating someone…", "Independence", ["I see them just as much", "A bit less", "A lot less", "I barely see them"]),
    ("f40", "Big decisions about your life you make…", "Independence", ["Alone", "With advice", "Together", "I let others decide"]),
    ("f41", "Travelling solo sounds…", "Independence", ["Amazing", "Fun", "Scary", "No way"]),
    ("f42", "Your sense of self comes most from…", "Independence", ["Me", "My work and passions", "My friends", "My relationship"]),
    ("f43", "Trying new things…", "Openness", ["Love it", "Like it", "Sometimes", "Avoid it"]),
    ("f44", "You let people see you cry…", "Openness", ["Easily", "With close people", "Rarely", "Never"]),
    ("f45", "Talking about your feelings is…", "Openness", ["Natural", "Fine", "Hard", "Painful"]),
    ("f46", "Would you date someone very different from you?", "Openness", ["Yes, love it", "Maybe", "Probably not", "No"]),
    ("f47", "Your past relationships you…", "Openness", ["Talk about openly", "Share when asked", "Keep vague", "Keep secret"]),
    ("f48", "The first move on a crush…", "Directness", ["I make it", "I give clear signs", "Small hints", "Never"]),
    ("f49", "Your biggest green flag in a partner is…", "Reassurance", ["Consistency", "Kindness", "Ambition", "Humour"]),
    ("f50", "Five years from now you want…", "Openness", ["Married or engaged", "A serious relationship", "Still exploring", "To be focused on me"]),
    ("f51", "The thing you repeat in every relationship…", "Independence", ["Losing myself", "Choosing badly", "Leaving early", "Staying too long"]),
    ("f52", "What would make you feel truly safe with someone?", "Reassurance", ["Words", "Consistency", "Space", "Loyalty"]),
    ("f53", "Meeting his friends for the first time, you…", "Openness", ["Win the room", "Chat with a couple of them", "Stay close to him", "Count down to leaving"], "EI"),
    ("f54", "The gift you'd love most is…", "Openness", ["Something symbolic", "A handwritten note", "Something useful", "Exactly what I asked for"], "SN"),
    ("f55", "When he hurts you by mistake, you need…", "Conflict comfort", ["To see he's truly sorry", "To feel he gets why it hurt", "It fixed", "Proof it won't happen again"], "TF"),
    ("f56", "Your calendar for next month is…", "Independence", ["Already full", "Mostly planned", "A few things in it", "Empty — I decide on the day"], "JP"),
    ("f57", "Him looking through your phone feels…", "Independence", ["Totally fine", "Fine if he asks", "Uneasy", "Never OK"]),
    ("f58", "When he brings up his ex, you…", "Reassurance", ["Listen calmly", "Get curious", "Feel a little jealous", "Change the subject"]),
    ("f59", "Your relationship on social media…", "Openness", ["I post it all", "Now and then", "Kept private", "Never, ever"]),
    ("f60", "After a bad day you want him to…", "Reassurance", ["Call me", "Come over", "Give me space", "Make me laugh"]),
])

TYPES = {
    "INFJ": ("The Quiet Confidant", "Feels everything, says half of it, and wants a love that means something."),
    "INFP": ("The Soft Romantic", "Imagines the whole love story before the second date."),
    "ENFJ": ("The Big-Hearted Leader", "Gives 110% and quietly hopes someone gives it back."),
    "ENFP": ("The Spark", "Falls for potential, lights up every room, gets bored of maybe."),
    "INTJ": ("The Strategist", "Has a five-year plan and a list of red flags. Rarely shares either."),
    "INTP": ("The Overthinker", "Analyses every text, then plays it cool."),
    "ENTJ": ("The Boss", "Has clear priorities and little time for mixed signals."),
    "ENTP": ("The Debater", "Loves a challenge, tests everyone, hates being bored."),
    "ISFJ": ("The Protector", "Remembers a partner’s coffee order and sometimes overlooks personal needs."),
    "ISFP": ("The Free Spirit", "Shows love in small, quiet ways and needs room to breathe."),
    "ESFJ": ("The Host", "Keeps everyone together and wants to feel valued."),
    "ESFP": ("The Life of the Party", "Lives in the moment and wants someone who keeps up."),
    "ISTJ": ("The Steady One", "Loyal, consistent and allergic to people who don't do what they said."),
    "ISTP": ("The Cool Head", "Calm on the outside, decides fast and leaves faster."),
    "ESTJ": ("The Organiser", "Plans the dates, sets the standards, expects effort."),
    "ESTP": ("The Thrill Seeker", "Chases the rush and needs someone who can match that energy."),
}


SETS = ("short", "extended", "further")

# Keep weights attached to the named answer across quiz edits. Choices without a
# weight still appear in saved summaries and contribute to questionnaire completion.
AXIS_SCORES = {
    "s1": {
        "Say it straight away": 4, "Hint and hope they notice": 2,
        "Wait until I've cooled down, then talk": 3, "Keep it to myself": 1,
    },
    "s2": {
        "Don't notice — I'm busy": 1, "Notice, but it's fine": 2,
        "Re-read the last message for clues": 3, "Feel it in my stomach and want an answer": 4,
    },
    "s3": {"Stand my ground calmly": 4, "Go quiet and avoid it": 1},
    "e6": {"Easy": 4, "Fine, if I explain why": 3, "Hard — I feel guilty": 2, "Almost impossible": 1},
    "e7": {"Say so the same day": 4, "Let it go once, then say it": 3, "Let it slide and resent it": 1},
    "e10": {"Barely": 1, "A normal amount": 2, "More than I'd admit": 3, "Constantly": 4},
    "e29": {"Daily": 4, "Every few days": 3, "Now and then": 2, "Never — actions matter": 1},
    "e31": {"First date": 4, "After a few weeks": 3, "After months": 2, "Almost never": 1},
    "e33": {"Ask directly": 4, "Hint": 2, "Wait for him": 1, "Pretend I don't": 1},
    "e36": {"Break it first": 4, "Wait a day, then talk": 4, "Wait for him": 2, "Out-silence him": 1},
    "f7": {"Soften it a lot": 2, "Tell her gently": 3, "Tell her straight": 4},
    "f17": {"Expect updates": 4, "Like a text or two": 3, "Hear about it later": 2, "Don't think about it": 1},
    "f26": {"Easy": 4, "OK": 3, "Hard": 2, "Something I don't do": 1},
    "f31": {"Kindly and clearly": 4, "With an excuse": 2, "By fading out": 1, "By ghosting": 1},
    "f32": {"I tell her": 4, "I hint": 2, "I distance myself": 1, "I let it go": 1},
    "f33": {"Easy": 4, "Fine": 3, "Uncomfortable": 2, "Almost impossible": 1},
    "f34": {"Ask him": 4, "Hint until he asks": 2, "Wait for him": 1, "Never ask": 1},
    "f35": {"Always": 4, "Usually": 3, "Sometimes": 2, "Rarely": 1},
    "f36": {"Stay first": 4, "Share the spotlight": 3, "Come second": 2, "Get paused": 1},
    "f39": {"I see them just as much": 4, "A bit less": 3, "A lot less": 2, "I barely see them": 1},
    "f40": {"Alone": 4, "With advice": 3, "Together": 2, "I let others decide": 1},
    "f44": {"Easily": 4, "With close people": 3, "Rarely": 2, "Never": 1},
    "f45": {"Natural": 4, "Fine": 3, "Hard": 2, "Painful": 1},
    "f47": {"Talk about openly": 4, "Share when asked": 3, "Keep vague": 2, "Keep secret": 1},
    "f48": {"I make it": 4, "I give clear signs": 3, "Small hints": 2, "Never": 1},
}
TYPE_SCORES = {
    "s9": {
        "Going out with friends": 4, "A small dinner with one or two people": 3,
        "A quiet night in": 2, "Total alone time, phone off": 1,
    },
    "s10": {
        "The vibe and what they could become": 4, "What they actually say": 2,
        "Facts — job, habits, how they treat the waiter": 1,
    },
    "s11": {
        "My heart, always": 4, "Mostly feelings, checked with logic": 3,
        "Mostly logic, checked with feelings": 2, "Cold hard facts": 1,
    },
    "s12": {
        "Planned to the hour": 4, "A rough plan": 3,
        "One thing booked, the rest open": 2, "No plan, see what happens": 1,
    },
    "e21": {"Talk to everyone": 4, "Stick with my group": 3, "Find one deep chat": 2},
    "e22": {
        "Gut feelings and patterns": 4, "Mostly my gut": 3,
        "Mostly what I've seen": 2, "Only what I've seen happen": 1,
    },
    "e23": {
        "Listen and comfort": 4, "Comfort, then suggest": 3,
        "Suggest, then comfort": 2, "Fix it straight away": 1,
    },
    "f4": {"Plan it for months": 4, "Plan the big bits": 3, "Figure it out as we go": 2, "Just do it": 1},
    "f5": {"I'm the most active": 4, "I chat a fair bit": 3, "I mostly read": 2},
    "f10": {"Ideas and possibilities": 4, "Stories and meaning": 3, "Practical skills": 2, "Facts and proof": 1},
    "f11": {
        "Pick who I feel more for": 4, "Mostly go with feelings": 3,
        "Mostly list pros and cons": 2, "Pros and cons only": 1,
    },
    "f16": {"Stress me out": 4, "Are fine with some notice": 3, "Are fun": 2, "Are the best thing ever": 1},
    "f53": {"Win the room": 4, "Chat with a couple of them": 3, "Stay close to him": 2},
}
BUILTIN_QUESTIONS = {row["id"]: row for row in SHORT + EXTENDED + FURTHER}
FORMER_TAGS = {("s3", "Stand my ground calmly"): "holds her ground",
               ("s6", "Tell them"): "says how she feels",
               ("s11", "My heart, always"): "leads with her heart",
               ("s11", "Cold hard facts"): "decides with her head"}


def _scored_question(row, built_in=False):
    legacy = built_in or any("score" in opt for opt in row["options"])
    if not legacy:
        return row
    original = BUILTIN_QUESTIONS.get(row["id"])
    indices = {opt["label"]: i for i, opt in enumerate(original["options"])} if original and original["q"] == row["q"] else {}
    options = []
    for opt in row["options"]:
        current = {k: v for k, v in opt.items() if k != "score"}
        i = indices.get(opt["label"])
        if i is not None:
            former_tag = FORMER_TAGS.get((row["id"], opt["label"]))
            if former_tag and current.get("tag") == former_tag:
                current["tag"] = original["options"][i]["tag"]
            for key, table, field in (("axis_score", AXIS_SCORES, "axis"), ("type_score", TYPE_SCORES, "dim")):
                score = table.get(row["id"], {}).get(opt["label"])
                if score is not None and row.get(field) == original.get(field):
                    current.setdefault(key, score)
        options.append(current)
    result = {**row, "options": options}
    if not any(opt.get("type_score") is not None for opt in options):
        result.pop("dim", None)
    return result


def with_quiz_scoring(quiz):
    """Upgrade stored positional weights by question and choice wording, preserving administrator content."""
    if quiz.get("scoring_version") == 1:
        return quiz
    return {**quiz, "scoring_version": 1, **{key: [_scored_question(q) for q in quiz[key]] for key in SETS}}


def valid_option_scores(opt, dim):
    if not isinstance(opt, dict) or not opt.get("label"):
        return False
    for key in ("axis_score", "type_score", "score"):
        if opt.get(key) is not None and (type(opt[key]) is not int or not 1 <= opt[key] <= 4):
            return False
    return opt.get("type_score") is None or dim in DIMS


_DEFAULT_SETS = {key: [_scored_question(q, built_in=True) for q in rows]
                 for key, rows in zip(SETS, (SHORT, EXTENDED, FURTHER))}


def default_quiz() -> dict:
    return {"version": VERSION, "scoring_version": 1, "axes": AXES, **_DEFAULT_SETS}


def _answer_scores(about, quiz, field, score_key):
    grouped = {}
    for section in SETS:
        for row in quiz.get(section) or []:
            choice = resolve_answer(about, quiz, row)
            if choice is None:
                continue
            score = row["options"][choice].get(score_key)
            if row.get(field) and type(score) is int and 1 <= score <= 4:
                grouped.setdefault(row[field], []).append(score)
    return grouped


def radar(about, quiz) -> list[dict]:
    values = _answer_scores(about, quiz, "axis", "axis_score")
    axes = []
    for axis in quiz["axes"]:
        scores = values.get(axis, [])
        value = round((sum(scores) / len(scores) - 1) * 100 / 3) if scores else None
        axes.append({"axis": axis, "score": value, "answered": len(scores)})
    return axes


def personality(about: dict, quiz: dict) -> dict:
    acc = _answer_scores(about, quiz, "dim", "type_score")
    dims = []
    for d in DIMS:
        vals = acc.get(d, [])
        pair = {"SN": "NS", "TF": "FT"}.get(d, d)
        balance = 2 * sum(vals) - 5 * len(vals)
        letter = pair[0] if balance > 0 else pair[1] if balance < 0 else ""
        pct = round((sum(vals) / len(vals) - 1) * 100 / 3) if vals else None
        dims.append({"pair": pair, "first_pct": pct, "letter": letter, "answered": len(vals)})
    guess = "".join(x["letter"] for x in dims) if all(x["letter"] for x in dims) else ""
    selected = (about or {}).get("mbti")
    self_set = selected in TYPES
    code = selected if self_set else guess
    if self_set:
        dims = [{**x, "first_pct": None, "letter": letter} for x, letter in zip(dims, code)]
    name, line = TYPES.get(code, ("", ""))
    return {"code": code, "guess": guess, "name": name, "line": line,
            "dims": dims, "self_set": self_set, "answered": sum(x["answered"] for x in dims)}


def about_summary(about: dict, quiz: dict) -> dict:
    answers = (about or {}).get("answers") or {}
    tags, lines = [], []
    done = {s: 0 for s in SETS}
    for set_name in SETS:
        for qq in quiz.get(set_name) or []:
            a = answers.get(qq["id"])
            choice = resolve_answer(about, quiz, qq)
            if choice is None:
                continue
            opt = qq["options"][choice]
            done[set_name] += 1
            if set_name == "short":
                tags.append(opt.get("tag") or opt["label"])
            note = f" (member note: {a['note'][:200]})" if a.get("note") else ""
            lines.append(f"- {qq['q']} -> {opt['label']}{note}")
    describe = ((about or {}).get("describe") or "").strip()
    n = {s: max(1, len(quiz.get(s) or [])) for s in SETS}
    score = round(done["short"] / n["short"] * 40 + (15 if len(describe) >= 15 else 0)
                  + done["extended"] / n["extended"] * 25 + done["further"] / n["further"] * 20)
    missing = [f"{n[s] - done[s]} {label} question{'s' if n[s] - done[s] > 1 else ''}" for s, label in
               (("short", "basics"), ("extended", "Go deeper"), ("further", "Values & communication")) if n[s] - done[s] > 0]
    if len(describe) < 15:
        missing.insert(1 if done["short"] < n["short"] else 0, "how your best friend describes you")
    questionnaire_complete = bool(sum(len(quiz.get(s) or []) for s in SETS)) and all(
        done[s] == len(quiz.get(s) or []) for s in SETS)
    return {"tags": tags, "lines": lines, "describe": describe, "short_done": done["short"], "missing": missing,
            "questionnaire_complete": questionnaire_complete,
            "extended_done": done["extended"], "further_done": done["further"], "score": score,
            "demographics": (about or {}).get("demographics") or {},
            "personality": personality(about, quiz)}
