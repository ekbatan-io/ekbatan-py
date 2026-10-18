package nprobe2;

import io.ekbatan.core.domain.GenericState;
import io.ekbatan.core.domain.Id;
import io.ekbatan.core.domain.Model;
import io.ekbatan.core.processor.AutoBuilder;

/** Area N2: @AutoBuilder on a class extending another model (Python twin: n2pkg/sub_model.py). */
public class SubclassProbe {
    public static class Wallet extends Model<Wallet, Id<Wallet>, GenericState> {
        protected <B extends Model.Builder<Id<Wallet>, B, Wallet, GenericState>> Wallet(Model.Builder<Id<Wallet>, B, Wallet, GenericState> b) {
            super(b);
        }

        @Override
        public Model.Builder<Id<Wallet>, ?, Wallet, GenericState> copy() {
            throw new UnsupportedOperationException();
        }
    }

    @AutoBuilder
    public static final class SavingsWallet extends Wallet {
        public final int rate = 0;

        SavingsWallet(SavingsWalletBuilder b) {
            super(b);
        }
    }
}
