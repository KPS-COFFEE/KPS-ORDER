package sa.kps.driver;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.File;
import java.io.FileNotFoundException;

/** One temporary app-private capture, shared only through explicit URI grants. */
public final class ProofProvider extends ContentProvider {
    @Override public boolean onCreate(){return true;}
    private File file(Uri uri){
        if(!"/photo.jpg".equals(uri.getPath()))throw new IllegalArgumentException("Unknown capture");
        return new File(getContext().getCacheDir(),"driver-proof.jpg");
    }
    @Override public String getType(Uri uri){file(uri);return "image/jpeg";}
    @Override public ParcelFileDescriptor openFile(Uri uri,String mode) throws FileNotFoundException {
        return ParcelFileDescriptor.open(file(uri),ParcelFileDescriptor.parseMode(mode));
    }
    @Override public Cursor query(Uri uri,String[] projection,String selection,String[] args,String sort){
        File f=file(uri);String[] cols=projection==null?new String[]{OpenableColumns.DISPLAY_NAME,OpenableColumns.SIZE}:projection;
        MatrixCursor cursor=new MatrixCursor(cols);Object[] row=new Object[cols.length];
        for(int i=0;i<cols.length;i++){if(OpenableColumns.DISPLAY_NAME.equals(cols[i]))row[i]="order-photo.jpg";else if(OpenableColumns.SIZE.equals(cols[i]))row[i]=f.length();}
        cursor.addRow(row);return cursor;
    }
    @Override public Uri insert(Uri u,ContentValues v){throw new UnsupportedOperationException();}
    @Override public int update(Uri u,ContentValues v,String s,String[] a){throw new UnsupportedOperationException();}
    @Override public int delete(Uri u,String s,String[] a){throw new UnsupportedOperationException();}
}
