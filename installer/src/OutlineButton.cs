using System;
using System.Drawing;
using System.Windows.Forms;

namespace AIPraat.Setup
{
    public static class SetupTheme
    {
        public static readonly Color Text=Color.FromArgb(32,41,56), Muted=Color.FromArgb(86,100,122),
            Border=Color.FromArgb(135,149,166), Line=Color.FromArgb(216,223,231),
            Primary=Color.FromArgb(36,93,181), Success=Color.FromArgb(24,112,70), Error=Color.FromArgb(179,38,30),
            Surface=Color.FromArgb(246,248,250);
        static readonly float dpi=ReadDpi();
        static float ReadDpi(){using(var graphics=Graphics.FromHwnd(IntPtr.Zero))return graphics.DpiX/96f;}
        public static int Px(int value){return (int)Math.Round(value*dpi);}
        public static Font Body {get{return new Font("Microsoft YaHei UI",10.5f);}}
        public static Font Small {get{return new Font("Microsoft YaHei UI",9f);}}
        public static string Hint(string text){return System.Text.RegularExpressions.Regex.Replace(text??"","[。.](?=\\s*(?:\\n|$))","").Replace("。","；");}
        public static void Divider(Panel panel){panel.Paint+=(s,e)=>{using(var pen=new Pen(Line))e.Graphics.DrawLine(pen,0,panel.Height-1,panel.Width,panel.Height-1);};}
        public static void FitToScreen(Form form){var area=Screen.FromControl(form).WorkingArea;form.Size=new Size(Math.Min(form.Width,area.Width),Math.Min(form.Height,area.Height));form.Location=new Point(area.Left+(area.Width-form.Width)/2,area.Top+(area.Height-form.Height)/2);}
        public static void AddRow(TableLayoutPanel stack,Control control)
        {
            int row=stack.RowCount++;stack.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            control.Margin=Padding.Empty;control.Dock=DockStyle.Top;stack.Controls.Add(control,0,row);
        }
        public static TableLayoutPanel Stack(){var table=new TableLayoutPanel{ColumnCount=1,AutoSize=true,AutoSizeMode=AutoSizeMode.GrowAndShrink,Dock=DockStyle.Top,Margin=Padding.Empty};table.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));return table;}
    }
    public sealed class OutlineButton : Button
    {
        bool primary;
        public bool Primary {get{return primary;}set{primary=value;Invalidate();}}
        public Color OutlineColor {get{return Enabled?SetupTheme.Border:Color.FromArgb(204,211,221);}}
        public OutlineButton()
        {
            SetStyle(ControlStyles.UserPaint|ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer,true);
            FlatStyle=FlatStyle.Flat;BackColor=Color.White;ForeColor=SetupTheme.Text;Cursor=Cursors.Hand;
            AccessibleRole=AccessibleRole.PushButton;
        }
        protected override void OnEnabledChanged(EventArgs e) { base.OnEnabledChanged(e); Invalidate(); }
        protected override void OnMouseEnter(EventArgs e){base.OnMouseEnter(e);Invalidate();}
        protected override void OnMouseLeave(EventArgs e){base.OnMouseLeave(e);Invalidate();}
        protected override void OnPaint(PaintEventArgs e)
        {
            bool hover=Enabled&&ClientRectangle.Contains(PointToClient(MousePosition));
            Color background=!Enabled?Color.FromArgb(244,245,247):Primary?(hover?Color.FromArgb(29,78,151):SetupTheme.Primary):hover?Color.FromArgb(240,244,248):Color.White;
            Color foreground=!Enabled?Color.FromArgb(105,117,134):Primary?Color.White:SetupTheme.Text;
            e.Graphics.Clear(background);
            using(var pen=new Pen(Primary&&Enabled?background:OutlineColor))e.Graphics.DrawRectangle(pen,0,0,Width-1,Height-1);
            TextRenderer.DrawText(e.Graphics,Text,Font,ClientRectangle,foreground,TextFormatFlags.HorizontalCenter|TextFormatFlags.VerticalCenter|TextFormatFlags.EndEllipsis);
            if(Focused&&Enabled)using(var pen=new Pen(Primary?Color.White:SetupTheme.Primary,SetupTheme.Px(2)))e.Graphics.DrawRectangle(pen,3,3,Width-7,Height-7);
        }
    }
    public sealed class SetupProgressBar : ProgressBar
    {
        public Color ProgressColor=SetupTheme.Primary;
        public SetupProgressBar(){SetStyle(ControlStyles.UserPaint|ControlStyles.AllPaintingInWmPaint|ControlStyles.OptimizedDoubleBuffer,true);}
        protected override void OnPaint(PaintEventArgs e){e.Graphics.Clear(Color.FromArgb(228,233,240));using(var brush=new SolidBrush(ProgressColor))e.Graphics.FillRectangle(brush,0,0,Width*(Value-Minimum)/Math.Max(1,Maximum-Minimum),Height);}
    }
}
