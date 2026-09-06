from pathlib import Path
text=Path('index.html').read_text(encoding='utf-8',errors='replace')
terms=['wa.me','واتساب','WhatsApp','whatsapp','sendOrder','send','product-price','productId','product-id','cart','checkout','DELIVERY','customer','location']
print('INDEX_LENGTH',len(text),'LINES',text.count('\n')+1)
for term in terms:
    print('\n===',term,'count=',text.count(term),'===')
    start=0
    for i in range(min(text.count(term),5)):
        pos=text.find(term,start)
        if pos<0: break
        a=max(0,pos-450); b=min(len(text),pos+850)
        snippet=text[a:b].replace('\r','')
        print(snippet)
        print('\n---')
        start=pos+len(term)
