// Uses existing Windows printer drivers. No raw LAN/ESC-POS connection.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.Drawing.Printing;
using System.Globalization;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

class KpsPrintBridge {
    const string Origin = "https://kps-order-emergency-v4.onrender.com";
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 4*1024*1024 };
    static readonly string DataDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "KPS-Print-Bridge");
    static string Token, Printer;
    static Dictionary<string,object> Obj(object value) { return (Dictionary<string,object>)value; }
    static string Str(Dictionary<string,object> value,string key) { return value.ContainsKey(key) && value[key]!=null ? Convert.ToString(value[key],CultureInfo.InvariantCulture) : ""; }
    static bool Bool(Dictionary<string,object> value,string key) { return value.ContainsKey(key) && Convert.ToBoolean(value[key]); }
    static Dictionary<string,object> Api(string path,object body) {
        var request=(HttpWebRequest)WebRequest.Create(Origin+"/api/printing/"+path);
        request.Method="POST"; request.ContentType="application/json; charset=utf-8";
        request.Headers["Origin"]=Origin; request.AllowAutoRedirect=false;
        request.Timeout=20000; request.ReadWriteTimeout=20000;
        if (!String.IsNullOrEmpty(Token)) request.Headers["Authorization"]="Bearer "+Token;
        byte[] bytes=Encoding.UTF8.GetBytes(Json.Serialize(body));request.ContentLength=bytes.Length;
        using(var stream=request.GetRequestStream()) stream.Write(bytes,0,bytes.Length);
        using(var response=request.GetResponse()) using(var reader=new StreamReader(response.GetResponseStream(),Encoding.UTF8))
            return Obj(Json.DeserializeObject(reader.ReadToEnd()));
    }
    // A flushed journal is written BEFORE invoking Print(). Never retry a job
    // found in the journal, even when acknowledgement was lost.
    static void Durable(string path,string text) {
        byte[] bytes=Encoding.UTF8.GetBytes(text);string temp=path+".new";
        using(var file=new FileStream(temp,FileMode.Create,FileAccess.Write,FileShare.None)) {file.Write(bytes,0,bytes.Length);file.Flush(true);}
        if(File.Exists(path)) File.Replace(temp,path,null); else File.Move(temp,path);
    }
    static string JobPath(string id) {
        if(!System.Text.RegularExpressions.Regex.IsMatch(id,"^print_[0-9a-f]{32}$"))throw new Exception("Invalid job identifier");
        return Path.Combine(DataDir,id+".json");
    }
    static void Ack(string id,string status) {Api("bridge/jobs/"+id+"/ack",new {status=status});}
    static string RecordedOutcome(Dictionary<string,object> record) {
        return Str(record,"status")=="submitted" ||
            (Str(record,"status")=="acknowledged" && Str(record,"outcome")=="submitted") ? "submitted" : "uncertain";
    }
    static void AcknowledgeRecord(string path,Action<string,string> acknowledge,bool replay=false) {
        var record=Obj(Json.DeserializeObject(File.ReadAllText(path,Encoding.UTF8)));
        if(Str(record,"status")=="acknowledged" && !replay)return;
        string id=Path.GetFileNameWithoutExtension(path),outcome=RecordedOutcome(record);
        try { acknowledge(id,outcome); }
        catch(WebException error) {
            var response=error.Response as HttpWebResponse;
            if(response==null || response.StatusCode!=HttpStatusCode.NotFound)throw;
            // A restored database or newly paired device may not own this
            // historic job. Preserve the journal; NEVER print it again.
            outcome="server_job_unavailable";
        }
        Durable(path,Json.Serialize(new {status="acknowledged",outcome=outcome}));
    }
    static void RecoverAcknowledgements() {
        foreach(string path in Directory.GetFiles(DataDir,"print_*.json"))AcknowledgeRecord(path,Ack);
    }
    static void DispatchOnce(Dictionary<string,object> job,string path,Action<Dictionary<string,object>> submit) {
        if(File.Exists(path))return;
        Durable(path,"{\"status\":\"started\"}");
        try {submit(job);Durable(path,"{\"status\":\"submitted\"}");}
        catch {Durable(path,"{\"status\":\"uncertain\"}");Console.WriteLine("Print uncertain. Check the Windows queue before requesting a reprint.");}
    }
    static void Configure() {
        Directory.CreateDirectory(DataDir);string config=Path.Combine(DataDir,"device.bin");
        string setupPath=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"setup.json");
        if(File.Exists(config) && !File.Exists(setupPath)) {
            byte[] plain=ProtectedData.Unprotect(File.ReadAllBytes(config),null,DataProtectionScope.CurrentUser);
            var saved=Obj(Json.DeserializeObject(Encoding.UTF8.GetString(plain)));
            Token=Str(saved,"token");Printer=Str(saved,"printer_name");
        } else {
            var printers=PrinterSettings.InstalledPrinters;
            if(printers.Count==0)throw new Exception("No installed Windows printers.");
            var setup=File.Exists(setupPath)?Obj(Json.DeserializeObject(File.ReadAllText(setupPath,Encoding.UTF8))):new Dictionary<string,object>();
            Application.EnableVisualStyles();
            using(var form=new Form {Text="KPS — ربط طابعة الفرع / Printer setup",Width=600,Height=340,StartPosition=FormStartPosition.CenterScreen,RightToLeft=RightToLeft.Yes,Font=new Font("Tahoma",11),FormBorderStyle=FormBorderStyle.FixedDialog,MaximizeBox=false}) {
                var title=new Label {Text="الفرع / Branch: "+Str(setup,"branch_name"),Left=20,Top=20,Width=540,Height=45};
                var list=new ComboBox {Left=20,Top=95,Width=540,DropDownStyle=ComboBoxStyle.DropDownList,RightToLeft=RightToLeft.No};
                foreach(string printer in printers)list.Items.Add(printer);
                var hint=new Label {Text="اختر الطابعة المعرفة على هذا الكمبيوتر / Select printer",Left=20,Top=65,Width=540,Height=25};
                var codeBox=new TextBox {Left=20,Top=140,Width=540,Text=Str(setup,"pairing_code"),Visible=!setup.ContainsKey("pairing_code"),RightToLeft=RightToLeft.No};
                var status=new Label {Left=20,Top=180,Width=540,Height=45,Text=File.Exists(config)?"سيستبدل ربط هذا الكمبيوتر بعد نجاح الربط الجديد / Replace this computer's connection":""};
                var connect=new Button {Text="ربط / Connect",Left=200,Top=240,Width=170,Height=40};
                form.Controls.AddRange(new Control[]{title,hint,list,codeBox,status,connect});
                connect.Click+=delegate {
                    if(list.SelectedIndex<0){status.Text="اختر الطابعة أولاً / Select a printer first";return;}
                    connect.Enabled=false;status.Text="جاري الربط / Connecting…";form.Refresh();
                    try {
                        Printer=Convert.ToString(list.SelectedItem);Token=null;
                        var paired=Api("bridge/pair",new {pairing_code=codeBox.Text.Trim(),printer_name=Printer});Token=Str(paired,"token");
                        form.DialogResult=DialogResult.OK;form.Close();
                    }catch(WebException error){var response=error.Response as HttpWebResponse;status.Text=response!=null && response.StatusCode==HttpStatusCode.Unauthorized?"انتهى رمز الربط؛ نزّل إعداد الفرع مجدداً / Download fresh branch setup":"تعذر الاتصال؛ تحقق من الإنترنت والصلاحية / Check connection and permission";connect.Enabled=true;}
                };
                if(form.ShowDialog()!=DialogResult.OK)throw new Exception("Setup cancelled; previous connection unchanged.");
            }
            byte[] encrypted=ProtectedData.Protect(Encoding.UTF8.GetBytes(Json.Serialize(new {token=Token,printer_name=Printer})),null,DataProtectionScope.CurrentUser);
            string pending=config+".new";
            using(var file=new FileStream(pending,FileMode.Create,FileAccess.Write,FileShare.None)) {file.Write(encrypted,0,encrypted.Length);file.Flush(true);}
            if(File.Exists(config))File.Replace(pending,config,null);else File.Move(pending,config);
            if(File.Exists(setupPath))File.Delete(setupPath);
            MessageBox.Show("تم الربط. ارجع للموقع لطباعة صفحة اختبار ثم اعتماد الطابعة.\nConnected. Return to the website to print a test and approve.","KPS",MessageBoxButtons.OK,MessageBoxIcon.Information);
        }
        var settings=new PrinterSettings {PrinterName=Printer};
        if(!settings.IsValid)throw new Exception("The paired Windows printer is not installed for this Windows user.");
        // Resume the paired device on the next sign-in of this Windows user.
        // No machine-wide privileges or changes to printer drivers are needed.
        using(var key=Microsoft.Win32.Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run")) {
            key.SetValue("KPS Print Bridge", "\""+System.Reflection.Assembly.GetExecutingAssembly().Location+"\"");
        }
        Console.WriteLine("Receipt layout: PAPER-FIT-20261012 / SALES-QR-V1 (full printable width QR).");
        Console.WriteLine("Printer: "+Printer+". Keep this window open. Ctrl+C stops the bridge.");
    }
    static List<Dictionary<string,object>> Lines(Dictionary<string,object> payload) {
        var result=new List<Dictionary<string,object>>();
        foreach(object line in (object[])payload["lines"])result.Add(Obj(line));
        return result;
    }
    static Image Logo() {
        string path=Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"logo.png");
        using(var original=Image.FromFile(path)) {
            var bitmap=new Bitmap(original.Width,original.Height);
            using(var g=Graphics.FromImage(bitmap)) using(var attributes=new ImageAttributes()) {
                attributes.SetColorMatrix(new ColorMatrix(new float[][] {
                    new float[]{0,0,0,0,0},new float[]{0,0,0,0,0},new float[]{0,0,0,0,0},
                    new float[]{0,0,0,1,0},new float[]{0,0,0,0,1}}));
                g.DrawImage(original,new Rectangle(0,0,bitmap.Width,bitmap.Height),0,0,original.Width,original.Height,GraphicsUnit.Pixel,attributes);
            }return bitmap;
        }
    }
    // All dimensions are hundredths of an inch. The Graphics origin is the
    // printer's printable origin, not the physical paper corner.
    static RectangleF ContentBounds(RectangleF page, RectangleF printable, float hardX, float hardY) {
        var physical=RectangleF.Intersect(page,printable);
        if(physical.Width<=16 || physical.Height<=16)throw new Exception("Printer reports an invalid printable area.");
        return new RectangleF(physical.Left-hardX+8,physical.Top-hardY+8,physical.Width-16,physical.Height-16);
    }
    static StringFormat LineFormat(bool ar,bool center) {
        var format=new StringFormat();
        if(ar)format.FormatFlags=StringFormatFlags.DirectionRightToLeft;
        format.Alignment=center?StringAlignment.Center:StringAlignment.Near;
        return format;
    }
    static bool IsRule(Dictionary<string,object> line) {
        string text=Str(line,"text").Trim();
        return text.Length>0 && text.Trim('─').Length==0;
    }
    static Font LineFont(Dictionary<string,object> line,Font regular,Font bold,Font caption,Font heading) {
        string role=Str(line,"role");
        if(role=="caption")return caption;
        if(role=="reference" || role=="item")return heading;
        return Bool(line,"bold")?bold:regular;
    }
    static float TextHeight(Graphics graphics,string text,Font font,float width,bool ar) {
        using(var format=LineFormat(ar,false))
            return graphics.MeasureString(text,font,(int)Math.Floor(width),format).Height;
    }
    static float LineHeight(Graphics graphics,Dictionary<string,object> line,Font font,float width,bool ar) {
        if(Str(line,"role")=="qr")return width+8;
        if(IsRule(line))return 12;
        if(Str(line,"role")=="row") {
            return Math.Max(TextHeight(graphics,Str(line,"label"),font,width*0.44f-4,ar),
                TextHeight(graphics,Str(line,"value"),font,width*0.56f-4,ar))+4;
        }
        return TextHeight(graphics,Str(line,"text"),font,width,ar)+3;
    }
    static void DrawLine(Graphics graphics,Dictionary<string,object> line,Font font,RectangleF box,bool ar) {
        if(Str(line,"role")=="qr") {
            var matrix=(IList)line["matrix"];int count=matrix.Count;
            if(count<21 || count>185)throw new Exception("Invalid QR dimensions");
            float cell=box.Width/count;
            graphics.FillRectangle(Brushes.White,box.Left,box.Top,box.Width,box.Width);
            for(int y=0;y<count;y++) {
                string row=Convert.ToString(matrix[y]);
                if(row.Length!=count)throw new Exception("Invalid QR row");
                for(int x=0;x<count;x++)if(row[x]=='1')
                    graphics.FillRectangle(Brushes.Black,box.Left+x*cell,box.Top+y*cell,cell,cell);
            }
            return;
        }
        if(IsRule(line)) {graphics.DrawLine(Pens.Black,box.Left,box.Top+6,box.Right,box.Top+6);return;}
        if(Str(line,"role")=="row") {
            float labelWidth=box.Width*0.44f-4,valueWidth=box.Width*0.56f-4;
            var labelBox=new RectangleF(ar?box.Right-labelWidth:box.Left,box.Top,labelWidth,box.Height);
            var valueBox=new RectangleF(ar?box.Left:box.Right-valueWidth,box.Top,valueWidth,box.Height);
            using(var labelFormat=LineFormat(ar,false)) using(var valueFormat=LineFormat(ar,false)) {
                valueFormat.Alignment=StringAlignment.Far;
                graphics.DrawString(Str(line,"label"),font,Brushes.Black,labelBox,labelFormat);
                graphics.DrawString(Str(line,"value"),font,Brushes.Black,valueBox,valueFormat);
            }
        } else using(var format=LineFormat(ar,Bool(line,"center")))
            graphics.DrawString(Str(line,"text"),font,Brushes.Black,box,format);
    }
    static void Print(Dictionary<string,object> job) {
        var payload=Obj(job["payload"]);var lines=Lines(payload);bool ar=Str(payload,"language")=="ar";
        int width=Convert.ToInt32(job["paper_mm"])==58?228:315;
        using(var document=new PrintDocument()) using(var regular=new Font("Arial",11)) using(var bold=new Font("Arial",12,FontStyle.Bold)) using(var caption=new Font("Arial",9)) using(var heading=new Font("Arial",13,FontStyle.Bold)) using(var logo=Logo()) {
            document.PrinterSettings.PrinterName=Printer;document.PrinterSettings.Copies=1;
            document.PrintController=new StandardPrintController();document.DocumentName="KPS "+Str(job,"id");
            document.OriginAtMargins=false;
            document.DefaultPageSettings.Margins=new Margins(8,8,8,8);
            document.DefaultPageSettings.PaperSize=new PaperSize("KPS thermal roll",width,1169);
            var settings=document.DefaultPageSettings;
            var measured=ContentBounds(new RectangleF(0,0,width,1169),settings.PrintableArea,settings.HardMarginX,settings.HardMarginY);
            // Use the driver's printable width (e.g. 72.1mm on an 80mm roll),
            // and the same RTL measurement used for drawing.
            float height=88+settings.HardMarginY;
            using(var graphics=document.PrinterSettings.CreateMeasurementGraphics(settings)) {
                foreach(var line in lines)height+=LineHeight(graphics,line,LineFont(line,regular,bold,caption,heading),measured.Width,ar);
            }
            settings.PaperSize=new PaperSize("KPS thermal roll",width,(int)Math.Min(30000,Math.Ceiling(height)));
            int index=0;bool first=true;
            document.PrintPage+=(sender,e)=> {
                var bounds=ContentBounds(e.PageBounds,e.PageSettings.PrintableArea,e.PageSettings.HardMarginX,e.PageSettings.HardMarginY);
                float left=bounds.Left,top=bounds.Top,available=bounds.Width;
                if(first) {e.Graphics.DrawImage(logo,left+(available-60)/2,top,60,60);top+=64;first=false;}
                while(index<lines.Count) {
                    var line=lines[index];var font=LineFont(line,regular,bold,caption,heading);
                    float lineHeight=LineHeight(e.Graphics,line,font,available,ar);
                    if(top+lineHeight>bounds.Bottom && top>bounds.Top) {e.HasMorePages=true;return;}
                    DrawLine(e.Graphics,line,font,new RectangleF(left,top,available,lineHeight),ar);
                    top+=lineHeight;index++;
                }e.HasMorePages=false;
            };
            document.Print(); // Only successful spool submission, never proof of paper.
        }
    }
    [STAThread]
    static int Main(string[] args) {
        if(args.Length>0 && args[0]=="--self-test") {
            var fixture=Obj(Json.DeserializeObject("{\"lines\":[{\"text\":\"KPS قهوة\",\"bold\":true,\"center\":true}]}"));
            if(Lines(fixture).Count!=1) return 1;
            // 80mm roll, 72.1mm printable width and asymmetric driver offsets.
            var area=ContentBounds(new RectangleF(0,0,315,800),new RectangleF(12,5,284,790),12,5);
            if(Math.Abs(area.Left-8)>0.01 || Math.Abs(area.Right-276)>0.01 || area.Bottom>790)return 2;
            var narrow=ContentBounds(new RectangleF(0,0,228,600),new RectangleF(10,4,208,590),10,4);
            if(narrow.Width!=192 || narrow.Left<0 || narrow.Right>208)return 3;
            using(var logo=Logo()) {if(logo.Width<1)return 1;}
            var matrix=new ArrayList();
            for(int y=0;y<29;y++)matrix.Add(y==14?new string('0',14)+"1"+new string('0',14):new string('0',29));
            var qrLine=new Dictionary<string,object>{{"role","qr"},{"matrix",matrix}};
            using(var bitmap=new Bitmap(290,290))using(var graphics=Graphics.FromImage(bitmap))using(var font=new Font("Arial",10)) {
                if(LineHeight(graphics,qrLine,font,290,false)!=298)return 4;
                DrawLine(graphics,qrLine,font,new RectangleF(0,0,290,290),false);
                if(bitmap.GetPixel(145,145).R!=0 || bitmap.GetPixel(5,5).R!=255)return 5;
            }
            // Exercise the actual durable journal and lost-ACK recovery without
            // opening a printer or contacting the server. Only this temp path is used.
            string testDir=Path.Combine(Path.GetTempPath(),"KPS-Print-Self-Test-"+Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(testDir);
            try {
                string id="print_"+new string('a',32),path=Path.Combine(testDir,id+".json");
                var job=new Dictionary<string,object>{{"id",id}};int submissions=0,acks=0;
                Action<Dictionary<string,object>> submit=delegate(Dictionary<string,object> ignored) {
                    if(Str(Obj(Json.DeserializeObject(File.ReadAllText(path,Encoding.UTF8))),"status")!="started")
                        throw new Exception("Journal was not durable before spool dispatch.");
                    submissions++;
                };
                DispatchOnce(job,path,submit);
                if(submissions!=1 || RecordedOutcome(Obj(Json.DeserializeObject(File.ReadAllText(path,Encoding.UTF8))))!="submitted")return 6;
                bool lostAck=false;
                try {AcknowledgeRecord(path,delegate(string jobId,string outcome) {throw new IOException("Simulated lost ACK");});}
                catch(IOException) {lostAck=true;}
                if(!lostAck || Str(Obj(Json.DeserializeObject(File.ReadAllText(path,Encoding.UTF8))),"status")!="submitted")return 7;
                DispatchOnce(job,path,submit);
                Action<string,string> acknowledge=delegate(string jobId,string outcome) {
                    if(jobId!=id || outcome!="submitted")throw new Exception("Recovered ACK changed a submitted outcome.");
                    acks++;
                };
                AcknowledgeRecord(path,acknowledge);AcknowledgeRecord(path,acknowledge);
                DispatchOnce(job,path,submit);AcknowledgeRecord(path,acknowledge,true);
                if(submissions!=1 || acks!=2)return 8;
                string failedId="print_"+new string('b',32),failedPath=Path.Combine(testDir,failedId+".json");
                int failedSubmissions=0;
                Action<Dictionary<string,object>> fail=delegate(Dictionary<string,object> ignored) {
                    failedSubmissions++;throw new IOException("Simulated ambiguous spool failure");
                };
                DispatchOnce(new Dictionary<string,object>{{"id",failedId}},failedPath,fail);
                DispatchOnce(new Dictionary<string,object>{{"id",failedId}},failedPath,fail);
                if(failedSubmissions!=1)return 9;
                AcknowledgeRecord(failedPath,delegate(string jobId,string outcome) {
                    if(jobId!=failedId || outcome!="uncertain")throw new Exception("Failed spool must remain uncertain.");
                });
                string crashId="print_"+new string('c',32),crashPath=Path.Combine(testDir,crashId+".json");
                Durable(crashPath,"{\"status\":\"started\"}");
                DispatchOnce(new Dictionary<string,object>{{"id",crashId}},crashPath,fail);
                if(failedSubmissions!=1)return 10;
                AcknowledgeRecord(crashPath,delegate(string jobId,string outcome) {
                    if(jobId!=crashId || outcome!="uncertain")throw new Exception("Interrupted spool must remain uncertain.");
                });
            } finally {Directory.Delete(testDir,true);}
            Console.WriteLine("Bridge compile, JSON, logo, durable dispatch and lost-ACK recovery checks passed; no printer used.");return 0;
        }
        bool created;
        using(var mutex=new Mutex(true,"Local\\KPSBranchPrintBridge",out created)) {
            if(!created){MessageBox.Show("أغلق نافذة برنامج الطباعة السابقة ثم افتح الإعداد مجدداً.\nClose the previous bridge window, then reopen setup.","KPS");return 1;}
            try {
                if(args.Length>0 && args[0]=="--re-pair") {
                    string config=Path.Combine(DataDir,"device.bin");
                    if(File.Exists(config))File.Move(config,config+"."+DateTime.UtcNow.Ticks+".old");
                }
                ServicePointManager.SecurityProtocol=SecurityProtocolType.Tls12;Configure();
                while(true) {
                    try {
                        RecoverAcknowledgements();var response=Api("bridge/next",new {});
                        if(response["job"]!=null) {
                            var job=Obj(response["job"]);string id=Str(job,"id"),path=JobPath(id);
                            DispatchOnce(job,path,Print);
                            // A duplicate delivery must replay its durable outcome,
                            // including submitted; it must not downgrade to uncertain.
                            AcknowledgeRecord(path,Ack,true);
                        }
                    }catch(WebException ex) {
                        var response=ex.Response as HttpWebResponse;
                        if(response!=null && (response.StatusCode==HttpStatusCode.Unauthorized || response.StatusCode==HttpStatusCode.Forbidden)) {
                            Console.WriteLine("Device access revoked or pairing no longer valid. Ask the manager to pair this computer again.");return 1;
                        }
                        Console.WriteLine("Connection unavailable; acknowledgements will retry. No automatic reprint.");
                    }
                    Thread.Sleep(3000);
                }
            }catch(Exception ex) {Console.WriteLine("Bridge stopped: "+ex.Message);return 1;}
        }
    }
}
