"""Bounded local PDF text, table and optional OCR extraction."""
import base64
import io
from urllib.parse import urlsplit

MAX_TEXT = 180000


def extract_pdf(pdf_base64, url, ocr=False):
    import pdfplumber
    body = base64.b64decode(pdf_base64,validate=True)
    if len(body)>2_000_000:
        raise ValueError('PDF exceeds 2 MB limit')
    text, spans, tables, warnings, unreadable = '', [], [], [], []
    truncated = False
    engine = None
    with pdfplumber.open(io.BytesIO(body)) as pdf:
        page_count = len(pdf.pages)
        truncated = page_count>30
        for number,page in enumerate(pdf.pages[:30],1):
            page_text = (page.extract_text() or '').strip()
            layout='page_reading_order'
            # Verified FTSE factsheets place a narrow information sidebar next to tables.
            # Separate only that known publisher/layout, using detected borders rather than fixed coordinates.
            if urlsplit(url).hostname=='research.ftserussell.com':
                borders=[float(e['x0']) for e in page.edges if e.get('orientation')=='v'
                         and .60*page.width<float(e['x0'])<.75*page.width
                         and float(e['bottom'])-float(e['top'])>.35*page.height]
                if borders:
                    divider=min(borders)
                    left=page.crop((0,0,divider,page.height)).extract_text() or ''
                    right=page.crop((divider,0,page.width,page.height)).extract_text() or ''
                    if left.strip() and right.strip():
                        page_text=left.strip()+'\n\n'+right.strip()
                        layout='ftse_main_then_sidebar'
            method, confidence = 'pdf_text', None
            if len(page_text)<40:
                if not ocr:
                    warnings.append(f'Page {number}: insufficient text; OCR not enabled.')
                else:
                    try:
                        from rapidocr import RapidOCR
                        import numpy as np
                        if page.width*page.height*(150/72)**2>20_000_000:
                            raise ValueError('OCR page exceeds 20-megapixel rendering limit')
                        engine = engine or RapidOCR()
                        with page.to_image(resolution=150).original as bitmap:
                            result = engine(np.asarray(bitmap))
                        page_text = '\n'.join(result.txts or ())
                        confidence = min((float(score) for score in (result.scores or ())),default=0)
                        method = 'local_ocr'
                        if confidence<.85:
                            warnings.append(f'Page {number}: low OCR confidence; verify against the page image.')
                    except ImportError:
                        warnings.append(f'Page {number}: local OCR dependencies are missing; install requirements-ocr.txt.')
                    except Exception as exc:
                        warnings.append(f'Page {number}: OCR failed ({type(exc).__name__}); verify this page manually.')
            if len(page_text)<40:
                unreadable.append(number)
            separator='\n\n' if text else ''
            available=MAX_TEXT-len(text)-len(separator)
            if available<=0:
                truncated=True; break
            start=len(text)+len(separator)
            excerpt=page_text[:available]
            text+=separator+excerpt
            spans.append({'page':number,'start':start,'end':len(text),'method':method,
                          'layout_method':layout,'ocr_confidence':confidence,'bbox':[0,0,float(page.width),float(page.height)]})
            # Tables are context alongside original text, never substitute quotations.
            try:
                found=page.find_tables()[:10]
                for table in found:
                    rows=table.extract()
                    tables.append({'page':number,'bbox':list(table.bbox),'rows':[
                        [(cell or '')[:500] for cell in row[:20]] for row in rows[:100]],
                        'rows_truncated':len(rows)>100,'columns_truncated':any(len(row)>20 for row in rows),
                        'verified':False})
            except Exception as exc:
                warnings.append(f'Page {number}: table extraction failed ({type(exc).__name__}).')
            if len(page_text)>available:
                truncated=True; break
        metadata=pdf.metadata or {}
    if len(text.strip())<40:
        raise ValueError('PDF has insufficient usable text. ' + ' '.join(warnings[:3]))
    return {'title':str(metadata.get('Title') or url.rsplit('/',1)[-1]),'language':None,
            'published_at':None,'updated_at':None,'date_evidence':None,'headings':[],
            'text':text,'text_truncated':truncated,'links':[],'page_spans':spans,
            'pdf_page_count':page_count,'tables':tables,'quality_warnings':warnings,'unreadable_pages':unreadable,
            'pdf_metadata_dates':{'created':metadata.get('CreationDate'),'modified':metadata.get('ModDate')},
            'extraction_note':'Text order and table associations require review. OCR quotations are recognized text, not verified original transcription.'}
